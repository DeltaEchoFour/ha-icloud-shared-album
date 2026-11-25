# iCloud Shared Album for Home Assistant

Reads **one public iCloud Shared Album** from its link, for browsing on a dashboard. No Apple ID, password or 2FA: the album's public link is the only thing it uses, so it can't see any other album, device or account data.

## Setup

1. In Photos on iPhone, open the shared album → people icon → turn on **Public Website** → **Share Link**.
2. Install this repository from the Marketplace (custom repository, category Integration) and restart.
3. **Settings → Devices & services → Add integration → iCloud Shared Album**, paste the link.

## Dashboard card

The integration loads `custom:icloud-album-card` on every dashboard (no separate install). Pick **iCloud Shared Album** in the card picker, or:

```yaml
type: custom:icloud-album-card
entity: sensor.family_photos_photos
mode: slideshow        # slideshow | grid | latest
interval: 10           # seconds per photo (slideshow)
order: shuffle         # shuffle | added | taken
columns: 3             # grid
max_photos: 0          # grid, 0 = all
aspect_ratio: 4/3      # slideshow/latest; leave empty to fill the card
fit: smart             # smart | cover | contain (smart: on a wide frame, portraits pair up side by side, or sit whole on a blurred copy)
caption: true          # month + "3 months ago"
tap: viewer            # viewer | more-info | none
```

## Changing the album

**Settings → Devices & services → iCloud Shared Album → ⋮ → Reconfigure** and paste a new link. The sensor and cards keep working.

## What you get

- `sensor.<album>_photos`: number of photos; the entity picture is the newest photo.
- Action `icloud_shared_album.get_photos` (returns a response): the photo list, newest first, each with `thumb` and `full` image URLs.
- Images are served by Home Assistant at `/api/icloud_shared_album/<key>/<photo>/<thumb|full>`. HA fetches Apple's short-lived signed links itself, so dashboard URLs never expire and browsers can cache them. `<key>` is a random secret per album; anyone holding a full image URL can view that photo, the same as anyone holding the album's public link.
- The album is checked for new photos every 30 minutes (change under the integration's **Configure**), or immediately with `homeassistant.update_entity` on the sensor.

HEIC images are converted to JPEG on the way out (via `pillow-heif`), so Chrome-based browsers and Android WebViews can show them. `icloud_shared_album.check_images` fetches every photo and reports its format, for troubleshooting. Videos are skipped. Both the newer `photos.icloud.com/shared/album/…` links and older `icloud.com/sharedalbum/#…` links work.

Request shapes adapted from [eyalgal/album_slideshow](https://github.com/eyalgal/album_slideshow) (MIT).
