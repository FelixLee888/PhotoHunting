from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import datetime
from pathlib import Path

from PIL import ExifTags, Image


IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".heic", ".webp"}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".avi", ".mkv"}

EXIF_TAGS = {value: key for key, value in ExifTags.TAGS.items()}
GPS_TAGS = {value: key for key, value in ExifTags.GPSTAGS.items()}


KNOWN_LOCATIONS = {
    "skye": {"country": "Scotland", "region": "Highlands", "city": "Isle of Skye", "place": "Skye"},
    "glencoe": {"country": "Scotland", "region": "Highlands", "city": "Glencoe", "place": "Glencoe"},
    "ben nevis": {"country": "Scotland", "region": "Highlands", "city": "Fort William", "place": "Ben Nevis"},
    "kamikochi": {"country": "Japan", "region": "Nagano", "city": "Matsumoto", "place": "Kamikochi"},
    "tokyo": {"country": "Japan", "region": "Tokyo", "city": "Tokyo", "place": "Tokyo"},
}


def compute_checksum(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    cleaned = value.replace("Z", "+00:00")
    for parser in ("%Y-%m-%dT%H:%M:%S", "%Y:%m:%d %H:%M:%S"):
        try:
            return datetime.strptime(value, parser)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(cleaned)
    except ValueError:
        return None


def _json_safe(value):
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, bytes):
        return value.hex()
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _json_safe(nested) for key, nested in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    try:
        return float(value)
    except (TypeError, ValueError):
        return str(value)


def _to_decimal(value, reference) -> float | None:
    if not value:
        return None
    degrees = float(value[0])
    minutes = float(value[1])
    seconds = float(value[2])
    result = degrees + (minutes / 60.0) + (seconds / 3600.0)
    if reference in {"S", "W"}:
        result *= -1
    return result


def extract_image_metadata(path: Path) -> dict:
    metadata: dict = {}
    try:
        with Image.open(path) as image:
            metadata["width"], metadata["height"] = image.size
            metadata["media_type"] = "image"
            exif = image.getexif()
            gps_info = {}
            for key, value in exif.items():
                tag = ExifTags.TAGS.get(key, key)
                if tag == "GPSInfo":
                    gps_info = {ExifTags.GPSTAGS.get(k, k): v for k, v in value.items()}
                else:
                    metadata[tag] = value
            latitude = _to_decimal(gps_info.get("GPSLatitude"), gps_info.get("GPSLatitudeRef"))
            longitude = _to_decimal(gps_info.get("GPSLongitude"), gps_info.get("GPSLongitudeRef"))
            metadata["latitude"] = latitude
            metadata["longitude"] = longitude
            metadata["camera_model"] = metadata.get("Model")
            metadata["lens"] = metadata.get("LensModel")
            metadata["focal_length"] = str(metadata.get("FocalLength")) if metadata.get("FocalLength") else None
            metadata["orientation"] = str(metadata.get("Orientation")) if metadata.get("Orientation") else None
            metadata["date_taken"] = parse_datetime(metadata.get("DateTimeOriginal"))
    except Exception:
        return {"media_type": "image"}
    return _json_safe(metadata)


def extract_video_metadata(path: Path) -> dict:
    command = [
        "ffprobe",
        "-v",
        "quiet",
        "-print_format",
        "json",
        "-show_streams",
        "-show_format",
        str(path),
    ]
    try:
        result = subprocess.run(command, capture_output=True, text=True, check=True)
        payload = json.loads(result.stdout)
    except Exception:
        return {"media_type": "video"}

    video_stream = next((stream for stream in payload.get("streams", []) if stream.get("codec_type") == "video"), {})
    metadata = {
        "media_type": "video",
        "width": int(video_stream.get("width", 0)) or None,
        "height": int(video_stream.get("height", 0)) or None,
        "duration": float(payload.get("format", {}).get("duration", 0.0)) or None,
        "codec": video_stream.get("codec_name"),
        "frame_rate": _parse_frame_rate(video_stream.get("r_frame_rate")),
        "date_taken": parse_datetime(payload.get("format", {}).get("tags", {}).get("creation_time")),
    }
    return metadata


def _parse_frame_rate(value: str | None) -> float | None:
    if not value or "/" not in value:
        return None
    numerator, denominator = value.split("/", maxsplit=1)
    try:
        denominator_value = float(denominator)
        return float(numerator) / denominator_value if denominator_value else None
    except ValueError:
        return None


def infer_location_from_path(path: Path) -> dict:
    normalized = " ".join(part.lower().replace("_", " ") for part in path.parts)
    for name, location in KNOWN_LOCATIONS.items():
        if name in normalized:
            return location.copy()

    tokens = [token for token in normalized.split() if token.isalpha()]
    if len(tokens) >= 2:
        guess = " ".join(token.capitalize() for token in tokens[-2:])
    elif tokens:
        guess = tokens[-1].capitalize()
    else:
        guess = None
    return {"place": guess} if guess else {}


def caption_and_tags_from_path(path: Path, media_type: str) -> tuple[str, list[str], list[str]]:
    words = [word for word in path.stem.replace("-", " ").replace("_", " ").split() if word]
    base_tags = [word.lower() for word in words[:6]]
    objects = []
    if any(word.lower() in {"cat", "dog", "boat", "ridge", "beach", "mountain"} for word in words):
        objects = [word.lower() for word in words if word.lower() in {"cat", "dog", "boat", "ridge", "beach", "mountain"}]
    caption = f"{media_type.title()} captured at {' '.join(words) or path.name}."
    return caption, list(dict.fromkeys(base_tags)), list(dict.fromkeys(objects))
