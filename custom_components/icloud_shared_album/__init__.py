"""iCloud Shared Album: read one public shared album, nothing else."""
from __future__ import annotations

import logging
from typing import Any

from aiohttp import ClientError, web
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

_LOGGER = logging.getLogger(__name__)
PLATFORMS = [Platform.SENSOR]
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

type AlbumConfigEntry = ConfigEntry[AlbumCoordinator]


def image_url(entry: ConfigEntry, photo_id: str, size: str) -> str:
    return IMAGE_PATH.format(key=entry.data[CONF_KEY], photo_id=photo_id, size=size)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    hass.http.register_view(AlbumImageView(hass))

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
    fetches the current signed link server-side. The <key> is a random secret
    per album (images in an <img> tag can't carry a login token), and the view
    only ever serves photos of that one album. Responses are cacheable, so a
    browser downloads each photo once.
    """

    url = "/api/icloud_shared_album/{key}/{photo_id}/{size}"
    name = "api:icloud_shared_album:image"
    requires_auth = False

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass

    async def get(self, request: web.Request, key: str, photo_id: str, size: str) -> web.StreamResponse:
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
                raise web.HTTPNotFound
            src = photo["thumb_url" if size == "thumb" else "full_url"]
            try:
                async with session.get(src) as upstream:
                    if upstream.status in (401, 403, 404, 410) and attempt == 0:
                        # Signed link expired: refresh the album once and retry.
                        await coord.async_refresh()
                        continue
                    if upstream.status != 200:
                        raise web.HTTPBadGateway
                    resp = web.StreamResponse(
                        headers={
                            "Content-Type": upstream.headers.get("Content-Type", "image/jpeg"),
                            "Cache-Control": "private, max-age=2592000, immutable",
                        }
                    )
                    if upstream.content_length:
                        resp.content_length = upstream.content_length
                    await resp.prepare(request)
                    async for chunk in upstream.content.iter_chunked(65536):
                        await resp.write(chunk)
                    await resp.write_eof()
                    return resp
            except (ClientError, TimeoutError) as err:
                _LOGGER.debug("image fetch failed for %s: %s", photo_id, err)
                raise web.HTTPBadGateway from err
        raise web.HTTPBadGateway
