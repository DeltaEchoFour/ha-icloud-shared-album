"""Constants for iCloud Shared Album."""
from datetime import timedelta

DOMAIN = "icloud_shared_album"
CONF_URL = "url"
CONF_KEY = "key"
CONF_SCAN_MINUTES = "scan_minutes"
DEFAULT_SCAN_MINUTES = 30
SERVICE_GET_PHOTOS = "get_photos"
IMAGE_PATH = "/api/icloud_shared_album/{key}/{photo_id}/{size}"
SIZES = ("thumb", "full")
MIN_SCAN = timedelta(minutes=5)
