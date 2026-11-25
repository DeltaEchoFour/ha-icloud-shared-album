"""Photo count sensor for a shared album."""
from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import AlbumConfigEntry, image_url
from .const import DOMAIN
from .coordinator import AlbumCoordinator


async def async_setup_entry(
    hass: HomeAssistant, entry: AlbumConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    async_add_entities([AlbumPhotosSensor(entry.runtime_data, entry)])


class AlbumPhotosSensor(CoordinatorEntity[AlbumCoordinator], SensorEntity):
    """Number of photos; attributes say when the list last changed."""

    _attr_has_entity_name = True
    _attr_translation_key = "photos"
    _attr_icon = "mdi:image-multiple"
    _attr_native_unit_of_measurement = "photos"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _unrecorded_attributes = frozenset({"newest_id", "newest_full", "last_fetch"})

    def __init__(self, coordinator: AlbumCoordinator, entry: AlbumConfigEntry) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_photos"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            manufacturer="Apple",
            model="iCloud Shared Album",
            entry_type=DeviceEntryType.SERVICE,
        )

    @property
    def _photos(self) -> list[dict[str, Any]]:
        return (self.coordinator.data or {}).get("photos") or []

    @property
    def native_value(self) -> int:
        return len(self._photos)

    @property
    def entity_picture(self) -> str | None:
        photos = self._photos
        return image_url(self._entry, photos[0]["id"], "thumb") if photos else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        photos = self._photos
        data = self.coordinator.data or {}
        return {
            "album": data.get("title"),
            "newest_id": photos[0]["id"] if photos else None,
            "newest_full": image_url(self._entry, photos[0]["id"], "full") if photos else None,
            "last_fetch": data.get("fetched"),
        }
