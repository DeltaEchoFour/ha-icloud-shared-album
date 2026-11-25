"""iCloud Shared Album: read one public shared album, nothing else."""
from __future__ import annotations

import logging
from typing import Any

import asyncio
import json
from pathlib import Path

from aiohttp import web
import voluptuous as vol

from homeassistant.components.http import HomeAssistantView
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv, entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.typing import ConfigType

from .const import CONF_KEY, DOMAIN, IMAGE_PATH, SERVICE_GET_PHOTOS, SIZES
from .coordinator import AlbumCoordinator
from .images import UpstreamError, check_photo

_LOGGER = logging.getLogger(__name__)
PLATFORMS = [Platform.SENSOR]
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

type AlbumConfigEntry = ConfigEntry[AlbumCoordinator]


def to_ref(photo_id: str) -> str:
    """URL-safe form of a photo id. Apple's ids are base64 and can contain / and +."""
    return photo_id.replace("+", "-").replace("/", "_")


def from_ref(ref: str) -> str:
    return ref.replace("-", "+").replace("_", "/")


def image_url(entry: ConfigEntry, photo_id: str, size: str) -> str:
    return IMAGE_PATH.format(key=entry.data[CONF_KEY], photo_id=to_ref(photo_id), size=size)


CARD_URL = "/icloud_shared_album_static"
CARD_FILE = "icloud-album-card.js"


async def _register_card(hass: HomeAssistant) -> None:
    """Serve custom:icloud-album-card and load it on every dashboard."""
    from homeassistant.components.frontend import add_extra_js_url
    from homeassistant.components.http import StaticPathConfig

    here = Path(__file__).parent
    version = await hass.async_add_executor_job(lambda: json.loads((here / "manifest.json").read_text())["version"])
    await hass.http.async_register_static_paths([StaticPathConfig(CARD_URL, str(here / "www"), True)])
    add_extra_js_url(hass, f"{CARD_URL}/{CARD_FILE}?v={version}")


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    hass.http.register_view(AlbumImageView(hass))
    try:
        await _register_card(hass)
    except Exception:  # noqa: BLE001 - the card is optional; the integration still works without it
        _LOGGER.exception("Couldn't register the iCloud album card")

    async def get_photos(call: ServiceCall) -> ServiceResponse:
        entries = [e for e in hass.config_entries.async_loaded_entries(DOMAIN)]
        wanted = call.data.get("entity_id")
        if wanted:
            reg = er.async_get(hass)
            ent = reg.async_get(wanted)
            entries = [e for e in entries if ent and ent.config_entry_id == e.entry_id]
            if not entries:
                raise ServiceValidationError(f"{wanted} is not an iCloud Shared Album sensor")
        limit = call.data.get("limit")
        albums: dict[str, Any] = {}
        for entry in entries:
            coord: AlbumCoordinator = entry.runtime_data
            data = coord.data or {}
            photos = data.get("photos") or []
            if limit:
                photos = photos[:limit]
            albums[entry.entry_id] = {
                "title": data.get("title") or entry.title,
                "count": len(data.get("photos") or []),
                "fetched": data.get("fetched"),
                "photos": [
                    {
                        "id": p["id"],
                        "taken": p.get("taken"),
                        "added": p.get("added"),
                        "w": p.get("width"),
                        "h": p.get("height"),
                        "thumb": image_url(entry, p["id"], "thumb"),
                        "full": image_url(entry, p["id"], "full"),
                    }
                    for p in photos
                ],
            }
        return {"albums": albums}

    hass.services.async_register(
        DOMAIN,
        SERVICE_GET_PHOTOS,
        get_photos,
        schema=vol.Schema(
            {
                vol.Optional("entity_id"): cv.entity_id,
                vol.Optional("limit"): vol.All(vol.Coerce(int), vol.Range(min=1)),
            }
        ),
        supports_response=SupportsResponse.ONLY,
    )

    async def check_images(call: ServiceCall) -> ServiceResponse:
        """Fetch every photo from Apple and report what came back (diagnostics)."""
        session = async_get_clientsession(hass)
        report: dict[str, Any] = {}
        for entry in hass.config_entries.async_loaded_entries(DOMAIN):
            coord: AlbumCoordinator = entry.runtime_data
            photos = (coord.data or {}).get("photos") or []
            sem = asyncio.Semaphore(4)

            async def one(p: dict[str, Any]) -> dict[str, Any]:
                async with sem:
                    return await check_photo(coord.images, session, p)

            results = await asyncio.gather(*(one(p) for p in photos))
            formats: dict[str, int] = {}
            for r in results:
                for size in ("thumb", "full"):
                    k = f"{size}:" + (r[size].get("format") if r[size]["ok"] else f"error {r[size].get('status')}")
                    formats[k] = formats.get(k, 0) + 1
            problems = [r for r in results if not (r["thumb"]["ok"] and r["full"]["ok"]) or {r["thumb"].get("format"), r["full"].get("format")} - {"jpeg", "png"}]
            report[entry.entry_id] = {"title": entry.title, "photos": len(results), "summary": formats, "not_plain_jpeg": problems}
        return report

    hass.services.async_register(DOMAIN, "check_images", check_images, supports_response=SupportsResponse.ONLY)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: AlbumConfigEntry) -> bool:
    coordinator = AlbumCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_reload))
    return True


async def _reload(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: AlbumConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


class AlbumImageView(HomeAssistantView):
    """Serve album images from stable URLs.

    Apple's signed image links expire, so the dashboard never sees them: it
    asks for /api/icloud_shared_album/<key>/<photo>/<size>, and this view
    fetches the current signed link server-side, converting HEIC to JPEG so
    any browser can show it. The <key> is a random secret per album (images in
    an <img> tag can't carry a login token), and the view only ever serves
    photos of that one album. Responses are cacheable, so a browser downloads
    each photo once.
    """

    url = "/api/icloud_shared_album/{key}/{photo_id}/{size}"
    name = "api:icloud_shared_album:image"
    requires_auth = False

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass

    async def get(self, request: web.Request, key: str, photo_id: str, size: str) -> web.Response:
        photo_id = from_ref(photo_id)
        if size not in SIZES:
            raise web.HTTPNotFound
        entry = next(
            (e for e in self.hass.config_entries.async_loaded_entries(DOMAIN) if e.data.get(CONF_KEY) == key),
            None,
        )
        if entry is None:
            raise web.HTTPNotFound
        coord: AlbumCoordinator = entry.runtime_data
        session = async_get_clientsession(self.hass)
        for attempt in range(2):
            photo = coord.by_id.get(photo_id)
            if photo is None:
                _LOGGER.debug("Unknown photo %s", photo_id)
                raise web.HTTPNotFound
            try:
                data, ctype = await coord.images.get(self.hass, session, photo_id, size, photo[f"{size}_url"])
            except UpstreamError as err:
                if attempt == 0 and err.status in (401, 403, 404, 410):
                    # Signed link expired: refresh the album once and retry.
                    await coord.async_refresh()
                    continue
                _LOGGER.warning("Couldn't serve %s %s (%s): %s", size, photo_id, photo.get(f"{size}_type") or "?", err)
                raise web.HTTPBadGateway from err
            return web.Response(
                body=data,
                content_type=ctype.split(";")[0] or "image/jpeg",
                headers={"Cache-Control": "private, max-age=2592000, immutable"},
            )
        raise web.HTTPBadGateway
