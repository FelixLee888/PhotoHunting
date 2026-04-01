from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageOps

from app.core.config import Settings
from app.models import MediaItem

try:
    from pillow_heif import register_heif_opener

    register_heif_opener()
except Exception:
    register_heif_opener = None  # type: ignore[assignment]

try:
    import pyheif
except Exception:
    pyheif = None


def preview_url_for_media(
    media_id: str,
    media_type: str | None,
    analysis_status: str | None,
    thumbnail_url: str | None = None,
    *,
    variant: str = "default",
) -> str | None:
    if thumbnail_url:
        return thumbnail_url
    if media_type == "image" and analysis_status == "completed":
        if variant == "tv":
            return f"/api/media/{media_id}/preview/tv"
        return f"/api/media/{media_id}/preview"
    return None


def ensure_preview(item: MediaItem, settings: Settings, *, variant: str = "default") -> Path | None:
    if item.media_type != "image" or item.analysis_status != "completed":
        return None

    source_path = Path(item.source_path)
    if not source_path.exists():
        return None

    cache_dir = Path(settings.preview_cache_dir).expanduser()
    if not cache_dir.is_absolute():
        cache_dir = Path.cwd() / cache_dir
    cache_dir.mkdir(parents=True, exist_ok=True)

    checksum = (item.checksum or "preview")[:16]
    suffix = "tv" if variant == "tv" else "preview"
    preview_path = cache_dir / f"{item.id}-{checksum}-{suffix}.jpg"
    if preview_path.exists():
        return preview_path

    image = _load_image(source_path)
    if image is None:
        return None

    try:
        image = ImageOps.exif_transpose(image)
        max_dimension, jpeg_quality = _preview_settings(settings, variant)
        image.thumbnail((max_dimension, max_dimension))
        if image.mode in {"RGBA", "LA"}:
            background = Image.new("RGB", image.size, (255, 255, 255))
            alpha = image.getchannel("A") if "A" in image.getbands() else None
            background.paste(image.convert("RGB"), mask=alpha)
            image = background
        elif image.mode != "RGB":
            image = image.convert("RGB")
        image.save(preview_path, format="JPEG", quality=jpeg_quality, optimize=True)
        return preview_path
    finally:
        image.close()


def _preview_settings(settings: Settings, variant: str) -> tuple[int, int]:
    if variant == "tv":
        return settings.tv_preview_max_dimension, settings.tv_preview_jpeg_quality
    return settings.preview_max_dimension, settings.preview_jpeg_quality


def _load_image(path: Path) -> Image.Image | None:
    try:
        with Image.open(path) as image:
            return image.copy()
    except Exception:
        if path.suffix.lower() not in {".heic", ".heif"} or pyheif is None:
            return None
        try:
            heif_image = pyheif.read(path)
            return Image.frombytes(
                heif_image.mode,
                heif_image.size,
                heif_image.data,
                "raw",
                heif_image.mode,
                heif_image.stride,
            )
        except Exception:
            return None
