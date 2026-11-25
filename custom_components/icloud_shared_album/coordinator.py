"""Polls one shared album."""
from __future__ import annotations

from datetime import timedelta
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import AlbumError, SharedAlbumClient
from .images import ImageStore
from .const import CONF_SCAN_MINUTES, CONF_URL, DEFAULT_SCAN_MINUTES, DOMAIN

_LOGGER = logging.getLogger(__name__)


class AlbumCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Holds the album's photo list and current signed image URLs."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        minutes = entry.options.get(CONF_SCAN_MINUTES, DEFAULT_SCAN_MINUTES)
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} {entry.title}",
            update_interval=timedelta(minutes=max(5, int(minutes))),
        )
        self.client = SharedAlbumClient(async_get_clientsession(hass), entry.data[CONF_URL])
        self.by_id: dict[str, dict[str, Any]] = {}
        self.images = ImageStore()

    async def _async_update_data(self) -> dict[str, Any]:
        try:
            title, photos = await self.client.async_fetch()
        except AlbumError as err:
            raise UpdateFailed(str(err)) from err
        # Newest first: when it was added to the album, else when it was taken.
        photos.sort(key=lambda p: p.get("added") or p.get("taken") or 0, reverse=True)
        self.by_id = {p["id"]: p for p in photos}
        return {"title": title, "photos": photos, "fetched": dt_util.utcnow().isoformat()}
