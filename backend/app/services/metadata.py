from __future__ import annotations

import hashlib
import json
import re
import subprocess
from datetime import datetime
from pathlib import Path

from PIL import ExifTags, Image


IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".heic", ".webp"}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".avi", ".mkv"}

EXIF_TAGS = {value: key for key, value in ExifTags.TAGS.items()}
GPS_TAGS = {value: key for key, value in ExifTags.GPSTAGS.items()}


KNOWN_LOCATIONS = {
    "isle of skye": {
        "country": "United Kingdom",
        "region": "Scotland",
        "city": "Isle of Skye",
        "place": "Isle of Skye",
        "latitude": 57.5357,
        "longitude": -6.2263,
    },
    "skye": {
        "country": "United Kingdom",
        "region": "Scotland",
        "city": "Isle of Skye",
        "place": "Isle of Skye",
        "latitude": 57.5357,
        "longitude": -6.2263,
    },
    "glencoe": {
        "country": "United Kingdom",
        "region": "Scotland",
        "city": "Glencoe",
        "place": "Glencoe",
        "latitude": 56.6825,
        "longitude": -5.1027,
    },
    "ben nevis": {
        "country": "United Kingdom",
        "region": "Scotland",
        "city": "Fort William",
        "place": "Ben Nevis",
        "latitude": 56.7969,
        "longitude": -5.0036,
    },
    "fort william": {
        "country": "United Kingdom",
        "region": "Scotland",
        "city": "Fort William",
        "place": "Fort William",
        "latitude": 56.8198,
        "longitude": -5.1052,
    },
    "ben cleuch": {
        "country": "United Kingdom",
        "region": "Scotland",
        "city": "Clackmannanshire",
        "place": "Ben Cleuch",
        "latitude": 56.1636,
        "longitude": -3.7344,
    },
    "ben ledi": {
        "country": "United Kingdom",
        "region": "Scotland",
        "city": "Callander",
        "place": "Ben Ledi",
        "latitude": 56.2516,
        "longitude": -4.2432,
    },
    "scotland": {
        "country": "United Kingdom",
        "region": "Scotland",
        "place": "Scotland",
        "latitude": 56.4907,
        "longitude": -4.2026,
    },
    "uk": {
        "country": "United Kingdom",
        "place": "United Kingdom",
        "latitude": 55.3781,
        "longitude": -3.4360,
    },
    "hungary": {
        "country": "Hungary",
        "place": "Hungary",
        "latitude": 47.1625,
        "longitude": 19.5033,
    },
    "sweden": {
        "country": "Sweden",
        "place": "Sweden",
        "latitude": 60.1282,
        "longitude": 18.6435,
    },
    "japan": {
        "country": "Japan",
        "place": "Japan",
        "latitude": 36.2048,
        "longitude": 138.2529,
    },
    "kamikochi": {
        "country": "Japan",
        "region": "Nagano",
        "city": "Matsumoto",
        "place": "Kamikochi",
        "latitude": 36.2453,
        "longitude": 137.6360,
    },
    "tokyo": {
        "country": "Japan",
        "region": "Tokyo",
        "city": "Tokyo",
        "place": "Tokyo",
        "latitude": 35.6764,
        "longitude": 139.6500,
    },
}

GENERIC_PATH_TOKENS = {
    "photo",
    "photos",
    "img",
    "image",
    "images",
    "trip",
    "trips",
    "video",
    "videos",
    "dji",
    "public",
    "volumes",
    "gptempdownload",
}

DATE_PREFIX_PATTERN = re.compile(r"^\d{4}(?:[-_ ]\d{2}){0,2}\s*")
WHITESPACE_PATTERN = re.compile(r"\s+")
NON_ALNUM_PATTERN = re.compile(r"[^a-z0-9]+")
LOCATION_ALIASES = tuple(sorted(KNOWN_LOCATIONS, key=len, reverse=True))


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


def _normalize_text(value: str) -> str:
    lowered = value.lower().replace("_", " ").replace("-", " ")
    collapsed = NON_ALNUM_PATTERN.sub(" ", lowered)
    return WHITESPACE_PATTERN.sub(" ", collapsed).strip()


def _clean_location_part(part: str) -> str:
    text = part.strip()
    if not text:
        return ""
    text = Path(text).stem
    text = text.replace("_", " ").replace("-", " ")
    text = DATE_PREFIX_PATTERN.sub("", text).strip()
    return WHITESPACE_PATTERN.sub(" ", text)


def _lookup_known_location(path: Path) -> dict:
    normalized_parts = [_normalize_text(_clean_location_part(part)) for part in path.parts[:-1]]
    normalized_path = f" {' '.join(part for part in normalized_parts if part)} "
    for alias in LOCATION_ALIASES:
        if f" {alias} " in normalized_path:
            return KNOWN_LOCATIONS[alias].copy()
    return {}


def _fallback_place_from_path(path: Path) -> dict:
    for part in path.parts[:-1]:
        cleaned = _clean_location_part(part)
        if not cleaned:
            continue
        words = []
        for raw_word in cleaned.split():
            normalized = _normalize_text(raw_word)
            if not normalized or normalized in GENERIC_PATH_TOKENS or normalized.isdigit():
                continue
            words.append(raw_word)
        if words:
            return {"place": " ".join(words[:4])}
    return {}


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
    known_location = _lookup_known_location(path)
    if known_location:
        return known_location
    return _fallback_place_from_path(path)


def caption_and_tags_from_path(path: Path, media_type: str) -> tuple[str, list[str], list[str]]:
    words = [word for word in path.stem.replace("-", " ").replace("_", " ").split() if word]
    base_tags = [word.lower() for word in words[:6]]
    objects = []
    if any(word.lower() in {"cat", "dog", "boat", "ridge", "beach", "mountain"} for word in words):
        objects = [word.lower() for word in words if word.lower() in {"cat", "dog", "boat", "ridge", "beach", "mountain"}]
    caption = f"{media_type.title()} captured at {' '.join(words) or path.name}."
    return caption, list(dict.fromkeys(base_tags)), list(dict.fromkeys(objects))
