"""Anonymous reader for one public iCloud Shared Album.

The only credential is the album's own public link. No Apple ID, password or
2FA is involved, and nothing outside that one album is reachable.

Apple serves shared albums from two public backends:

* CloudKit (``https://photos.icloud.com/shared/album/TOKEN``, iOS 26+ links):
  ``records/resolve`` turns the short token into a shared zone plus an
  anonymous access token; ``shared/records/query`` then pages through
  ``CPLMaster`` (image files) and ``CPLAsset`` (metadata) records whose
  derivatives carry signed download URLs.
* Shared streams (``https://www.icloud.com/sharedalbum/#TOKEN``, older links):
  ``webstream`` lists photos and ``webasseturls`` signs their derivatives.

The request shapes follow eyalgal/album_slideshow (MIT), which reverse
engineered both backends.
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
from datetime import datetime
from typing import Any
from urllib.parse import quote, urlencode

from aiohttp import ClientError, ClientSession, ClientTimeout

_LOGGER = logging.getLogger(__name__)

_TIMEOUT = ClientTimeout(total=60, connect=15)
_MAX_ASSETS = 20_000

BACKEND_CLOUDKIT = "cloudkit"
BACKEND_STREAMS = "sharedstreams"

_BASE62 = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
_TOKEN_CHARS = frozenset(_BASE62 + "-_")

# --- CloudKit -------------------------------------------------------------
_CK_RESOLVE_HOST = "https://ckdatabasews.icloud.com"
_CK_CONTAINER = "com.apple.photos.cloud"
_CK_BUILD = "2626"
_CK_RECORD_TYPE = "CPLAssetAndMasterByAssetDateWithoutHiddenOrDeleted"
_CK_PAGE = 200
_HEADERS = {
    "Content-Type": "text/plain",
    "Origin": "https://www.icloud.com",
    "Referer": "https://www.icloud.com/",
    "Accept": "application/json",
}
# Image derivatives on a CPLMaster, smallest first.
_CK_RES = (
    ("resJPEGThumbRes", "resJPEGThumbWidth", "resJPEGThumbHeight"),
    ("resJPEGMedRes", "resJPEGMedWidth", "resJPEGMedHeight"),
    ("resJPEGLargeRes", "resJPEGLargeWidth", "resJPEGLargeHeight"),
    ("resOriginalRes", "resOriginalWidth", "resOriginalHeight"),
)
_BROWSER_SAFE = {"public.jpeg", "public.png"}
_VIDEO_HINTS = ("movie", "video", "mpeg-4", "quicktime")

# --- shared streams -------------------------------------------------------
_URL_BATCH = 25


class AlbumError(Exception):
    """The album could not be read."""


class AlbumNotPublic(AlbumError):
    """The link resolved but the album is not shared as a public website."""


def parse_link(url: str) -> tuple[str, str] | None:
    """Return ``(token, backend)`` from a pasted share link, or ``None``."""
    text = (url or "").strip()
    if not text:
        return None
    lowered = text.lower()
    backend = (
        BACKEND_CLOUDKIT
        if "/shared/album/" in lowered or "photos.icloud.com" in lowered
        else BACKEND_STREAMS
    )
    if "#" in text:
        text = text.rsplit("#", 1)[1]
    elif "/" in text:
        text = text.rstrip("/").rsplit("/", 1)[1]
    token = text.split("?", 1)[0].strip()
    if token and all(ch in _TOKEN_CHARS for ch in token):
        return token, backend
    return None


def _v(fields: Any, name: str) -> Any:
    field = fields.get(name) if isinstance(fields, dict) else None
    return field.get("value") if isinstance(field, dict) else None


def _int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _iso_ms(value: Any) -> int | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp() * 1000)
    except ValueError:
        return None


def _filename(fields: dict[str, Any]) -> str:
    raw = _v(fields, "filenameEnc")
    if isinstance(raw, str) and raw:
        try:
            return base64.b64decode(raw).decode("utf-8", "replace")
        except (ValueError, TypeError):
            pass
    return "image.jpg"


def _ck_derivatives(master: dict[str, Any]) -> list[tuple[int, int | None, str, str]]:
    """Return ``(width, height, url, file_type)`` per derivative, smallest first.

    Derivatives a browser can show (JPEG/PNG) are kept ahead of the rest; a
    HEIC-only photo still gets its derivatives and is converted on the way out.
    """
    item_type = str(_v(master, "itemType") or "").lower()
    name = quote(_filename(master), safe="")
    out: list[tuple[int, int | None, str, str]] = []
    for res_key, w_key, h_key in _CK_RES:
        res = master.get(res_key)
        val = res.get("value") if isinstance(res, dict) else None
        url = val.get("downloadURL") if isinstance(val, dict) else None
        if not url:
            continue
        ftype = str(_v(master, res_key[:-3] + "FileType") or (item_type if res_key == "resOriginalRes" else "")).lower()
        if res_key == "resOriginalRes" and item_type not in _BROWSER_SAFE:
            continue
        out.append((_int(_v(master, w_key)) or 0, _int(_v(master, h_key)), url.replace("${f}", name), ftype))
    safe = [d for d in out if not d[3] or d[3] in _BROWSER_SAFE]
    out = safe or out
    out.sort(key=lambda d: d[0])
    return out


def _pick(derivs: list[tuple], min_width: int) -> tuple:
    """Smallest derivative at least ``min_width`` wide, else the largest."""
    for d in derivs:
        if d[0] >= min_width:
            return d
    return derivs[-1]


def parse_ck_records(records: list[Any]) -> list[dict[str, Any]]:
    """Join CPLMaster + CPLAsset records into photo dicts."""
    masters: dict[str, dict[str, Any]] = {}
    assets: list[dict[str, Any]] = []
    for rec in records:
        if not isinstance(rec, dict):
            continue
        if rec.get("recordType") == "CPLMaster":
            masters[rec.get("recordName")] = rec.get("fields") or {}
        elif rec.get("recordType") == "CPLAsset":
            assets.append(rec)
    photos: list[dict[str, Any]] = []
    seen: set[str] = set()
    for asset in assets:
        af = asset.get("fields") or {}
        if _v(af, "isHidden") or _v(af, "trashReason"):
            continue
        ref = _v(af, "masterRef")
        master_id = ref.get("recordName") if isinstance(ref, dict) else None
        master = masters.get(master_id)
        if not master or not master_id or master_id in seen:
            continue
        if any(h in str(_v(master, "itemType") or "").lower() for h in _VIDEO_HINTS):
            continue
        derivs = _ck_derivatives(master)
        if not derivs:
            continue
        seen.add(master_id)
        thumb = _pick(derivs, 320)
        large = derivs[-1] if derivs[-1][0] <= 2560 or len(derivs) == 1 else _pick(derivs, 1600)
        photos.append(
            {
                "id": master_id,
                "taken": _int(_v(af, "assetDate")),
                "added": _int(_v(af, "addedDate")) or _int((asset.get("created") or {}).get("timestamp")),
                "width": large[0] or None,
                "height": large[1],
                "thumb_url": thumb[2],
                "full_url": large[2],
                "thumb_type": thumb[3],
                "full_type": large[3],
                "item_type": str(_v(master, "itemType") or ""),
            }
        )
    return photos


def _ck_title(result: dict[str, Any]) -> str | None:
    share = result.get("share") if isinstance(result, dict) else None
    title = _v((share or {}).get("fields"), "cloudkit.title")
    return title.strip() if isinstance(title, str) and title.strip() else None


def _partition_host(token: str) -> str:
    chars = token[1:2] if token[:1] == "A" else token[1:3]
    num = 0
    for ch in chars:
        idx = _BASE62.find(ch)
        if idx < 0:
            return "p01-sharedstreams.icloud.com"
        num = num * 62 + idx
    return f"p{num:02d}-sharedstreams.icloud.com"


class SharedAlbumClient:
    """Reads one public shared album."""

    def __init__(self, session: ClientSession, url: str) -> None:
        parsed = parse_link(url)
        if not parsed:
            raise AlbumError("not an iCloud shared album link")
        self.token, self.backend = parsed
        self._session = session
        self._stream_host = _partition_host(self.token)

    async def _post(self, url: str, body: dict[str, Any]) -> tuple[int, Any, Any]:
        try:
            async with self._session.post(url, data=json.dumps(body), headers=_HEADERS, timeout=_TIMEOUT) as resp:
                raw = await resp.read()
                try:
                    payload = json.loads(raw) if raw else None
                except ValueError:
                    payload = None
                return resp.status, payload, resp.headers
        except (ClientError, asyncio.TimeoutError) as err:
            raise AlbumError(f"iCloud request failed: {err}") from err

    async def async_fetch(self) -> tuple[str | None, list[dict[str, Any]]]:
        """Return ``(album title, photos)``."""
        if self.backend == BACKEND_CLOUDKIT:
            return await self._fetch_cloudkit()
        return await self._fetch_streams()

    # ---------------------------------------------------------------- CloudKit
    async def _fetch_cloudkit(self) -> tuple[str | None, list[dict[str, Any]]]:
        url = (
            f"{_CK_RESOLVE_HOST}/database/1/{_CK_CONTAINER}/production/public/records/resolve"
            f"?ckjsBuildVersion={_CK_BUILD}&shortGUID={self.token}"
        )
        status, payload, _ = await self._post(url, {"shortGUIDs": [{"value": self.token}]})
        if status != 200 or not isinstance(payload, dict):
            raise AlbumError(f"resolve failed: HTTP {status}")
        results = payload.get("results") or []
        if not results:
            raise AlbumError("the link did not resolve to an album")
        result = results[0]
        zone = result.get("zoneID")
        access = result.get("anonymousPublicAccess") or {}
        if not zone or not access.get("token") or not access.get("databasePartition"):
            raise AlbumNotPublic("album is not shared as a public website")
        query = urlencode(
            {
                "remapEnums": "true",
                "getCurrentSyncToken": "true",
                "sharing_url_key": self.token,
                "publicAccessAuthToken": access["token"],
            }
        )
        qurl = f"{access['databasePartition']}/database/1/{_CK_CONTAINER}/production/shared/records/query?{query}"
        records: list[Any] = []
        marker: str | None = None
        while len(records) < _MAX_ASSETS:
            body: dict[str, Any] = {"zoneID": zone, "query": {"recordType": _CK_RECORD_TYPE}, "resultsLimit": _CK_PAGE}
            if marker:
                body["continuationMarker"] = marker
            status, page, _ = await self._post(qurl, body)
            if status != 200 or not isinstance(page, dict):
                raise AlbumError(f"records query failed: HTTP {status}")
            batch = page.get("records") or []
            records.extend(batch)
            marker = page.get("continuationMarker")
            if not batch or not marker:
                break
        return _ck_title(result), parse_ck_records(records)

    # ---------------------------------------------------------- shared streams
    async def _stream_post(self, path: str, body: dict[str, Any]) -> Any:
        for _ in range(3):
            url = f"https://{self._stream_host}/{self.token}/sharedstreams/{path}"
            status, payload, headers = await self._post(url, body)
            if status == 330:
                host = (payload or {}).get("X-Apple-MMe-Host") or headers.get("X-Apple-MMe-Host")
                if not host:
                    break
                self._stream_host = host
                continue
            if status != 200:
                raise AlbumError(f"{path} failed: HTTP {status}")
            return payload
        raise AlbumError(f"{path} failed: redirect loop")

    async def _fetch_streams(self) -> tuple[str | None, list[dict[str, Any]]]:
        stream = await self._stream_post("webstream", {"streamCtag": None})
        items = [p for p in (stream or {}).get("photos") or [] if p.get("mediaAssetType") != "video"]
        guids = [p["photoGuid"] for p in items if p.get("photoGuid")]
        urls: dict[str, str] = {}
        for i in range(0, len(guids), _URL_BATCH):
            res = await self._stream_post("webasseturls", {"photoGuids": guids[i : i + _URL_BATCH]})
            for checksum, info in ((res or {}).get("items") or {}).items():
                urls[checksum] = f"https://{info['url_location']}{info['url_path']}"
        photos: list[dict[str, Any]] = []
        for p in items:
            derivs = []
            for d in (p.get("derivatives") or {}).values():
                if d.get("checksum") in urls:
                    derivs.append((_int(d.get("width")) or 0, _int(d.get("height")), urls[d["checksum"]], ""))
            if not derivs:
                continue
            derivs.sort(key=lambda d: d[0])
            thumb, large = _pick(derivs, 320), derivs[-1]
            photos.append(
                {
                    "id": p["photoGuid"],
                    "taken": _iso_ms(p.get("dateCreated")),
                    "added": None,
                    "width": large[0] or None,
                    "height": large[1],
                    "thumb_url": thumb[2],
                    "full_url": large[2],
                }
            )
        return stream.get("streamName"), photos
