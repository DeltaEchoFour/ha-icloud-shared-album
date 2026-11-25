"""Fetch album images from Apple and make sure a browser can show them."""
from __future__ import annotations

import asyncio
from collections import OrderedDict
import io
import logging
from typing import Any

from aiohttp import ClientError, ClientSession, ClientTimeout

_LOGGER = logging.getLogger(__name__)
_TIMEOUT = ClientTimeout(total=45, connect=15)
_HEIF_BRANDS = {b"heic", b"heix", b"hevc", b"hevx", b"heim", b"heis", b"hevm", b"hevs", b"mif1", b"msf1"}
_MAX_PX = {"thumb": 640, "full": 2560}
_CACHE_BYTES = 96 * 1024 * 1024


class UpstreamError(Exception):
    def __init__(self, status: int | None, detail: str) -> None:
        super().__init__(detail)
        self.status = status


def sniff(data: bytes) -> str:
    """Name the image format from its first bytes."""
    head = data[:16]
    if head.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if head.startswith(b"\x89PNG"):
        return "png"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "webp"
    if head[:3] == b"GIF":
        return "gif"
    if head[4:8] == b"ftyp":
        brand = head[8:12]
        if brand in _HEIF_BRANDS:
            return "heif"
        if brand in (b"avif", b"avis"):
            return "avif"
        return "ftyp:" + brand.decode("ascii", "replace")
    return "unknown"


def _heif_to_jpeg(data: bytes, max_px: int) -> bytes:
    import pillow_heif  # imported lazily: only HEIC photos need it
    from PIL import Image, ImageOps

    pillow_heif.register_heif_opener()
    with Image.open(io.BytesIO(data)) as im:
        im = ImageOps.exif_transpose(im).convert("RGB")
        im.thumbnail((max_px, max_px))
        out = io.BytesIO()
        im.save(out, "JPEG", quality=85, optimize=True)
        return out.getvalue()


class ImageStore:
    """Small in-memory cache of browser-ready images, keyed by photo and size."""

    def __init__(self) -> None:
        self._cache: OrderedDict[tuple[str, str], tuple[bytes, str]] = OrderedDict()
        self._bytes = 0
        self._locks: dict[tuple[str, str], asyncio.Lock] = {}

    async def fetch_raw(self, session: ClientSession, url: str) -> tuple[bytes, str]:
        try:
            async with session.get(url, timeout=_TIMEOUT) as resp:
                if resp.status != 200:
                    raise UpstreamError(resp.status, f"HTTP {resp.status}")
                return await resp.read(), resp.headers.get("Content-Type", "")
        except (ClientError, asyncio.TimeoutError) as err:
            raise UpstreamError(None, str(err) or type(err).__name__) from err

    async def get(self, hass, session: ClientSession, photo_id: str, size: str, url: str) -> tuple[bytes, str]:
        key = (photo_id, size)
        hit = self._cache.get(key)
        if hit:
            self._cache.move_to_end(key)
            return hit
        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            hit = self._cache.get(key)
            if hit:
                return hit
            data, ctype = await self.fetch_raw(session, url)
            kind = sniff(data)
            if kind == "heif":
                try:
                    data = await hass.async_add_executor_job(_heif_to_jpeg, data, _MAX_PX[size])
                except ImportError as err:
                    raise UpstreamError(None, "HEIC photo but pillow-heif is not installed") from err
                except Exception as err:  # noqa: BLE001 - a bad file must not break the view
                    raise UpstreamError(None, f"HEIC conversion failed: {err}") from err
                ctype = "image/jpeg"
            elif kind in ("jpeg", "png", "webp", "gif", "avif"):
                ctype = "image/" + kind
            self._put(key, (data, ctype))
            self._locks.pop(key, None)
            return data, ctype

    def _put(self, key: tuple[str, str], value: tuple[bytes, str]) -> None:
        self._cache[key] = value
        self._bytes += len(value[0])
        while self._bytes > _CACHE_BYTES and self._cache:
            _, (old, _) = self._cache.popitem(last=False)
            self._bytes -= len(old)

    def clear(self) -> None:
        self._cache.clear()
        self._bytes = 0


async def check_photo(store: ImageStore, session: ClientSession, photo: dict[str, Any]) -> dict[str, Any]:
    """Fetch both sizes of one photo straight from Apple and describe them."""
    result: dict[str, Any] = {"id": photo["id"], "item_type": photo.get("item_type")}
    for size in ("thumb", "full"):
        try:
            data, ctype = await store.fetch_raw(session, photo[f"{size}_url"])
            result[size] = {"ok": True, "declared_type": photo.get(f"{size}_type"), "content_type": ctype, "format": sniff(data), "bytes": len(data)}
        except UpstreamError as err:
            result[size] = {"ok": False, "declared_type": photo.get(f"{size}_type"), "status": err.status, "error": str(err)}
    return result
