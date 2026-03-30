from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import warnings
from datetime import datetime
from pathlib import Path
from typing import Any

from dateutil import parser as date_parser
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
    "share",
    "public",
    "volumes",
    "gptempdownload",
}

DATE_PREFIX_PATTERN = re.compile(r"^\d{4}(?:[-_ ]\d{2}){0,2}\s*")
TRIP_FOLDER_PATTERN = re.compile(r"^(?P<date>\d{4}-\d{2}-\d{2})\s+(?P<name>.+\S)$")
DATE_ONLY_FOLDER_PATTERN = re.compile(r"^\d{4}(?:[-_ ]\d{2}){0,2}$")
WHITESPACE_PATTERN = re.compile(r"\s+")
NON_ALNUM_PATTERN = re.compile(r"[^a-z0-9]+")
LOCATION_ALIASES = tuple(sorted(KNOWN_LOCATIONS, key=len, reverse=True))
ISO6709_PATTERN = re.compile(
    r"(?P<latitude>[+-]\d{2}(?:\.\d+)?)(?P<longitude>[+-]\d{3}(?:\.\d+)?)(?P<altitude>[+-]\d+(?:\.\d+)?)?/?"
)
FFMPEG_DURATION_PATTERN = re.compile(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)")
FFMPEG_VIDEO_PATTERN = re.compile(
    r"Video:\s*(?P<codec>[^,]+).*?(?P<width>\d{2,5})x(?P<height>\d{2,5})(?:.*?(?P<fps>\d+(?:\.\d+)?)\s*fps)?",
    re.IGNORECASE,
)
FFMPEG_AUDIO_PATTERN = re.compile(
    r"Audio:\s*(?P<codec>[^,]+)(?:,.*?(?P<sample_rate>\d+)\s*Hz)?(?:,.*?(?P<channels>mono|stereo|\d+(?:\.\d+)?))?",
    re.IGNORECASE,
)
TEXT_METADATA_KEYS = (
    "ImageDescription",
    "XPTitle",
    "XPComment",
    "XPSubject",
    "XPKeywords",
    "UserComment",
)
VIDEO_LOCATION_TAG_KEYS = (
    "location",
    "location-eng",
    "location_eng",
    "gpscoordinates",
    "gpsposition",
    "com.apple.quicktime.location.iso6709",
    "com.apple.quicktime.location.iso6709-eng",
)


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
        try:
            return date_parser.parse(value)
        except (TypeError, ValueError, OverflowError):
            return None


def metadata_to_json_safe(value):
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, bytes):
        return _decode_text_value(value)
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): metadata_to_json_safe(nested) for key, nested in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [metadata_to_json_safe(item) for item in value]
    number_value = _rational_to_float(value)
    if number_value is not None:
        return number_value
    try:
        return float(value)
    except (TypeError, ValueError):
        return str(value)


def _rational_to_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if hasattr(value, "numerator") and hasattr(value, "denominator"):
        try:
            denominator = float(value.denominator)
            return float(value.numerator) / denominator if denominator else None
        except (TypeError, ValueError, ZeroDivisionError):
            return None
    if isinstance(value, (tuple, list)) and len(value) == 2:
        try:
            denominator = float(value[1])
            return float(value[0]) / denominator if denominator else None
        except (TypeError, ValueError, ZeroDivisionError):
            return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _coerce_int(value: Any) -> int | None:
    number_value = _rational_to_float(value)
    if number_value is None:
        return None
    return int(round(number_value))


def _coerce_float(value: Any) -> float | None:
    return _rational_to_float(value)


def _decode_text_value(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        cleaned = value.replace("\x00", "").strip()
        return cleaned or None
    if isinstance(value, bytes):
        for encoding in ("utf-8", "utf-16-le", "latin-1"):
            try:
                cleaned = value.decode(encoding, errors="ignore").replace("\x00", "").strip()
                if cleaned:
                    return cleaned
            except Exception:
                continue
        return None
    if isinstance(value, (tuple, list)) and value and all(isinstance(item, int) for item in value):
        return _decode_text_value(bytes(value))
    text = str(value).strip()
    return text or None


def _normalize_exif_value(value: Any) -> Any:
    if isinstance(value, (dict, list, tuple, set, bytes, Path, datetime)):
        return metadata_to_json_safe(value)
    number_value = _rational_to_float(value)
    if number_value is not None:
        return number_value
    return value


def _image_info_metadata(info: dict[str, Any]) -> dict[str, Any]:
    allowed_keys = {
        "dpi",
        "jfif",
        "jfif_density",
        "jfif_unit",
        "progressive",
        "progression",
        "icc_profile",
        "comment",
        "description",
        "Software",
        "XML:com.adobe.xmp",
    }
    result = {}
    for key, value in info.items():
        if key == "exif":
            continue
        if key in allowed_keys or isinstance(value, (str, int, float, bool, tuple, list, dict, bytes)):
            result[key] = metadata_to_json_safe(value)
    return result


def _build_lens_value(lens_model: Any, lens_info: Any) -> str | None:
    lens_model_value = _decode_text_value(lens_model)
    if lens_model_value:
        return lens_model_value
    if isinstance(lens_info, (tuple, list)):
        numeric = [value for value in (_coerce_float(item) for item in lens_info) if value is not None]
        if numeric:
            return " / ".join(f"{value:g}" for value in numeric)
    return _decode_text_value(lens_info)


def _format_exposure_time(value: Any) -> str | None:
    seconds = _coerce_float(value)
    if seconds is None or seconds <= 0:
        return None
    if seconds >= 1:
        formatted = f"{seconds:.3f}".rstrip("0").rstrip(".")
        return f"{formatted}s"
    reciprocal = round(1 / seconds)
    if reciprocal > 0:
        return f"1/{reciprocal}"
    return f"{seconds:.6f}s".rstrip("0").rstrip(".")


def _format_aperture(value: Any) -> str | None:
    aperture = _coerce_float(value)
    if aperture is None:
        return None
    return f"f/{aperture:.1f}".rstrip("0").rstrip(".")


def _build_gps_timestamp(gps_info: dict[str, Any]) -> str | None:
    date_stamp = _decode_text_value(gps_info.get("GPSDateStamp"))
    time_stamp = gps_info.get("GPSTimeStamp")
    if not date_stamp or not time_stamp:
        return None
    parts = []
    for component in time_stamp:
        number_value = _coerce_float(component)
        if number_value is None:
            return None
        parts.append(number_value)
    hours = int(parts[0]) if len(parts) > 0 else 0
    minutes = int(parts[1]) if len(parts) > 1 else 0
    seconds = parts[2] if len(parts) > 2 else 0
    return f"{date_stamp}T{hours:02d}:{minutes:02d}:{seconds:06.3f}Z"


def _extract_rotation(stream: dict[str, Any]) -> int | None:
    tags = stream.get("tags") or {}
    rotation_value = tags.get("rotate")
    if rotation_value is not None:
        rotation = _coerce_int(rotation_value)
        if rotation is not None:
            return rotation
    for side_data in stream.get("side_data_list", []) or []:
        if "rotation" in side_data:
            rotation = _coerce_int(side_data.get("rotation"))
            if rotation is not None:
                return rotation
    return None


def _extract_video_location(tags: dict[str, Any]) -> dict[str, Any]:
    normalized_tags = {str(key).lower(): value for key, value in tags.items()}
    for key in VIDEO_LOCATION_TAG_KEYS:
        raw_value = normalized_tags.get(key)
        parsed = _parse_location_value(raw_value)
        if parsed:
            parsed["location_tag"] = _decode_text_value(raw_value)
            return parsed
    return {}


def _parse_location_value(value: Any) -> dict[str, float]:
    text = _decode_text_value(value)
    if not text:
        return {}
    match = ISO6709_PATTERN.search(text)
    if match:
        latitude = _coerce_float(match.group("latitude"))
        longitude = _coerce_float(match.group("longitude"))
        altitude = _coerce_float(match.group("altitude"))
        if latitude is not None and longitude is not None:
            parsed = {"latitude": latitude, "longitude": longitude}
            if altitude is not None:
                parsed["gps_altitude"] = altitude
            return parsed
    parts = [part.strip() for part in re.split(r"[,\s]+", text.strip("/")) if part.strip()]
    if len(parts) >= 2:
        latitude = _coerce_float(parts[0])
        longitude = _coerce_float(parts[1])
        if latitude is not None and longitude is not None:
            return {"latitude": latitude, "longitude": longitude}
    return {}


def _format_orientation(rotation: int | None) -> str | None:
    if rotation is None:
        return None
    return f"rotate {rotation}"


def _maybe_run_json_command(command: list[str]) -> dict[str, Any] | None:
    executable = shutil.which(command[0])
    if not executable:
        return None
    try:
        result = subprocess.run([executable, *command[1:]], capture_output=True, text=True, check=True)
        return json.loads(result.stdout)
    except Exception:
        return None


def _ffprobe_payload(path: Path) -> dict[str, Any] | None:
    return _maybe_run_json_command(
        [
            "ffprobe",
            "-v",
            "quiet",
            "-print_format",
            "json",
            "-show_streams",
            "-show_format",
            str(path),
        ]
    )


def _ffmpeg_fallback_metadata(path: Path) -> dict[str, Any]:
    executable = shutil.which("ffmpeg")
    if not executable:
        return {"media_type": "video"}
    try:
        result = subprocess.run(
            [executable, "-hide_banner", "-i", str(path)],
            capture_output=True,
            text=True,
            check=False,
        )
    except Exception:
        return {"media_type": "video"}
    output = result.stderr or result.stdout
    metadata: dict[str, Any] = {"media_type": "video"}
    duration_match = FFMPEG_DURATION_PATTERN.search(output)
    if duration_match:
        hours, minutes, seconds = duration_match.groups()
        metadata["duration"] = (int(hours) * 3600) + (int(minutes) * 60) + float(seconds)
    video_match = FFMPEG_VIDEO_PATTERN.search(output)
    if video_match:
        metadata["codec"] = video_match.group("codec").strip()
        metadata["width"] = _coerce_int(video_match.group("width"))
        metadata["height"] = _coerce_int(video_match.group("height"))
        metadata["frame_rate"] = _coerce_float(video_match.group("fps"))
    audio_match = FFMPEG_AUDIO_PATTERN.search(output)
    if audio_match:
        metadata["audio_codec"] = _decode_text_value(audio_match.group("codec"))
        metadata["audio_sample_rate"] = _coerce_int(audio_match.group("sample_rate"))
        metadata["audio_channels"] = _decode_text_value(audio_match.group("channels"))
        metadata["has_audio"] = True
    return metadata


def _normalize_text(value: str) -> str:
    lowered = value.lower().replace("_", " ").replace("-", " ")
    collapsed = NON_ALNUM_PATTERN.sub(" ", lowered)
    return WHITESPACE_PATTERN.sub(" ", collapsed).strip()


def _has_meaningful_folder_text(value: str) -> bool:
    for char in value:
        if char.isalnum():
            return True
    return False


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
    degrees = _coerce_float(value[0])
    minutes = _coerce_float(value[1])
    seconds = _coerce_float(value[2])
    if degrees is None or minutes is None or seconds is None:
        return None
    result = degrees + (minutes / 60.0) + (seconds / 3600.0)
    if reference in {"S", "W"}:
        result *= -1
    return result


def extract_image_metadata(path: Path) -> dict:
    metadata: dict[str, Any] = {"media_type": "image"}
    try:
        with Image.open(path) as image:
            metadata["width"], metadata["height"] = image.size
            metadata["format"] = image.format
            metadata["mode"] = image.mode
            metadata["bands"] = list(image.getbands())
            metadata["is_animated"] = bool(getattr(image, "is_animated", False))
            metadata["frame_count"] = int(getattr(image, "n_frames", 1) or 1)
            image_info = _image_info_metadata(image.info)
            if image_info:
                metadata["image_info"] = image_info
            if hasattr(image, "getxmp"):
                try:
                    with warnings.catch_warnings():
                        warnings.simplefilter("ignore", UserWarning)
                        xmp = image.getxmp()
                except Exception:
                    xmp = None
                if xmp:
                    metadata["xmp"] = xmp
            exif = image.getexif()
            exif_tags: dict[str, Any] = {}
            gps_info: dict[str, Any] = {}
            for key, value in exif.items():
                tag = ExifTags.TAGS.get(key, key)
                if tag == "GPSInfo":
                    gps_info = {
                        ExifTags.GPSTAGS.get(k, k): _normalize_exif_value(v)
                        for k, v in value.items()
                    }
                else:
                    exif_tags[str(tag)] = _normalize_exif_value(value)
            if exif_tags:
                metadata["exif"] = exif_tags
            if gps_info:
                metadata["gps"] = metadata_to_json_safe(gps_info)
            latitude = _to_decimal(gps_info.get("GPSLatitude"), gps_info.get("GPSLatitudeRef"))
            longitude = _to_decimal(gps_info.get("GPSLongitude"), gps_info.get("GPSLongitudeRef"))
            altitude = _coerce_float(gps_info.get("GPSAltitude"))
            if altitude is not None and gps_info.get("GPSAltitudeRef") in {1, "1"}:
                altitude *= -1
            make = _decode_text_value(exif_tags.get("Make"))
            model = _decode_text_value(exif_tags.get("Model"))
            metadata["latitude"] = latitude
            metadata["longitude"] = longitude
            metadata["gps_altitude"] = altitude
            metadata["gps_timestamp"] = _build_gps_timestamp(gps_info)
            metadata["gps_img_direction"] = _coerce_float(gps_info.get("GPSImgDirection"))
            metadata["camera_make"] = make
            metadata["camera_model"] = model or make
            metadata["lens"] = _build_lens_value(exif_tags.get("LensModel"), exif_tags.get("LensInfo"))
            focal_length = _coerce_float(exif_tags.get("FocalLength"))
            metadata["focal_length_mm"] = focal_length
            metadata["focal_length"] = f"{focal_length:g}mm" if focal_length is not None else None
            metadata["focal_length_35mm"] = _coerce_int(exif_tags.get("FocalLengthIn35mmFilm"))
            metadata["orientation"] = _decode_text_value(exif_tags.get("Orientation")) or (
                str(_coerce_int(exif_tags.get("Orientation"))) if exif_tags.get("Orientation") is not None else None
            )
            metadata["date_taken"] = parse_datetime(_decode_text_value(exif_tags.get("DateTimeOriginal")))
            metadata["date_digitized"] = parse_datetime(_decode_text_value(exif_tags.get("DateTimeDigitized")))
            metadata["date_modified_exif"] = parse_datetime(_decode_text_value(exif_tags.get("DateTime")))
            metadata["software"] = _decode_text_value(exif_tags.get("Software"))
            metadata["artist"] = _decode_text_value(exif_tags.get("Artist"))
            metadata["copyright"] = _decode_text_value(exif_tags.get("Copyright"))
            metadata["description"] = next(
                (
                    text
                    for text in (_decode_text_value(exif_tags.get(key)) for key in TEXT_METADATA_KEYS)
                    if text
                ),
                None,
            )
            metadata["iso"] = next(
                (
                    value
                    for value in (
                        _coerce_int(exif_tags.get("PhotographicSensitivity")),
                        _coerce_int(exif_tags.get("ISOSpeedRatings")),
                        _coerce_int(exif_tags.get("ISO")),
                    )
                    if value is not None
                ),
                None,
            )
            metadata["aperture"] = _format_aperture(exif_tags.get("FNumber"))
            metadata["exposure_time"] = _format_exposure_time(exif_tags.get("ExposureTime"))
            metadata["shutter_speed_value"] = _coerce_float(exif_tags.get("ShutterSpeedValue"))
            metadata["brightness_value"] = _coerce_float(exif_tags.get("BrightnessValue"))
            metadata["exposure_bias"] = _coerce_float(exif_tags.get("ExposureBiasValue"))
            metadata["subject_distance_m"] = _coerce_float(exif_tags.get("SubjectDistance"))
            metadata["digital_zoom_ratio"] = _coerce_float(exif_tags.get("DigitalZoomRatio"))
            metadata["flash"] = _decode_text_value(exif_tags.get("Flash")) or (
                str(_coerce_int(exif_tags.get("Flash"))) if exif_tags.get("Flash") is not None else None
            )
            metadata["white_balance"] = _decode_text_value(exif_tags.get("WhiteBalance")) or (
                str(_coerce_int(exif_tags.get("WhiteBalance"))) if exif_tags.get("WhiteBalance") is not None else None
            )
    except Exception:
        return {"media_type": "image"}
    return metadata


def extract_video_metadata(path: Path) -> dict:
    payload = _ffprobe_payload(path)
    if payload is None:
        return _ffmpeg_fallback_metadata(path)

    format_info = payload.get("format") or {}
    streams = payload.get("streams") or []
    video_stream = next((stream for stream in streams if stream.get("codec_type") == "video"), {})
    audio_stream = next((stream for stream in streams if stream.get("codec_type") == "audio"), {})
    format_tags = format_info.get("tags") or {}
    video_tags = video_stream.get("tags") or {}
    audio_tags = audio_stream.get("tags") or {}
    merged_tags = {**format_tags, **video_tags, **audio_tags}
    rotation = _extract_rotation(video_stream)
    location_metadata = _extract_video_location(merged_tags)

    metadata: dict[str, Any] = {
        "media_type": "video",
        "width": _coerce_int(video_stream.get("width")),
        "height": _coerce_int(video_stream.get("height")),
        "duration": _coerce_float(format_info.get("duration")) or _coerce_float(video_stream.get("duration")),
        "codec": _decode_text_value(video_stream.get("codec_name")),
        "codec_long_name": _decode_text_value(video_stream.get("codec_long_name")),
        "profile": _decode_text_value(video_stream.get("profile")),
        "frame_rate": _parse_frame_rate(video_stream.get("avg_frame_rate")) or _parse_frame_rate(video_stream.get("r_frame_rate")),
        "bit_rate": _coerce_int(video_stream.get("bit_rate")) or _coerce_int(format_info.get("bit_rate")),
        "pixel_format": _decode_text_value(video_stream.get("pix_fmt")),
        "display_aspect_ratio": _decode_text_value(video_stream.get("display_aspect_ratio")),
        "sample_aspect_ratio": _decode_text_value(video_stream.get("sample_aspect_ratio")),
        "color_space": _decode_text_value(video_stream.get("color_space")),
        "color_transfer": _decode_text_value(video_stream.get("color_transfer")),
        "color_primaries": _decode_text_value(video_stream.get("color_primaries")),
        "field_order": _decode_text_value(video_stream.get("field_order")),
        "rotation": rotation,
        "orientation": _format_orientation(rotation),
        "date_taken": parse_datetime(
            _decode_text_value(format_tags.get("creation_time"))
            or _decode_text_value(video_tags.get("creation_time"))
            or _decode_text_value(format_tags.get("com.apple.quicktime.creationdate"))
        ),
        "camera_make": _decode_text_value(format_tags.get("com.apple.quicktime.make")) or _decode_text_value(format_tags.get("make")),
        "camera_model": _decode_text_value(format_tags.get("com.apple.quicktime.model")) or _decode_text_value(format_tags.get("model")),
        "software": _decode_text_value(format_tags.get("encoder")) or _decode_text_value(format_tags.get("software")),
        "audio_codec": _decode_text_value(audio_stream.get("codec_name")),
        "audio_codec_long_name": _decode_text_value(audio_stream.get("codec_long_name")),
        "audio_channels": audio_stream.get("channel_layout") or _coerce_int(audio_stream.get("channels")),
        "audio_sample_rate": _coerce_int(audio_stream.get("sample_rate")),
        "audio_bit_rate": _coerce_int(audio_stream.get("bit_rate")),
        "has_audio": bool(audio_stream),
        "container": {
            "format_name": _decode_text_value(format_info.get("format_name")),
            "format_long_name": _decode_text_value(format_info.get("format_long_name")),
            "probe_score": _coerce_int(format_info.get("probe_score")),
            "stream_count": len(streams),
        },
        "video_stream": {
            "codec_name": _decode_text_value(video_stream.get("codec_name")),
            "codec_long_name": _decode_text_value(video_stream.get("codec_long_name")),
            "profile": _decode_text_value(video_stream.get("profile")),
            "bit_rate": _coerce_int(video_stream.get("bit_rate")),
            "nb_frames": _coerce_int(video_stream.get("nb_frames")),
            "level": _coerce_int(video_stream.get("level")),
        },
        "audio_stream": {
            "codec_name": _decode_text_value(audio_stream.get("codec_name")),
            "codec_long_name": _decode_text_value(audio_stream.get("codec_long_name")),
            "channels": _coerce_int(audio_stream.get("channels")),
            "channel_layout": _decode_text_value(audio_stream.get("channel_layout")),
            "sample_rate": _coerce_int(audio_stream.get("sample_rate")),
            "bit_rate": _coerce_int(audio_stream.get("bit_rate")),
        }
        if audio_stream
        else {},
        "format_tags": metadata_to_json_safe(format_tags),
        "video_stream_tags": metadata_to_json_safe(video_tags),
        "audio_stream_tags": metadata_to_json_safe(audio_tags),
    }
    metadata.update(location_metadata)
    return metadata


def _parse_frame_rate(value: str | None) -> float | None:
    if not value:
        return None
    if "/" not in value:
        return _coerce_float(value)
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


def infer_trip_from_path(path: Path) -> dict[str, str]:
    for part in reversed(path.parts[:-1]):
        candidate = WHITESPACE_PATTERN.sub(" ", Path(part).name.strip())
        if not candidate:
            continue
        if not _has_meaningful_folder_text(candidate):
            continue
        normalized_candidate = _normalize_text(candidate)
        tokens = [token for token in normalized_candidate.split() if token]
        if DATE_ONLY_FOLDER_PATTERN.match(candidate):
            continue
        if normalized_candidate in GENERIC_PATH_TOKENS:
            continue
        if tokens and all(token.isdigit() or token in GENERIC_PATH_TOKENS for token in tokens):
            continue
        match = TRIP_FOLDER_PATTERN.match(candidate)
        if match:
            trip_date = match.group("date")
            trip_event_name = WHITESPACE_PATTERN.sub(" ", match.group("name")).strip()
            if not trip_event_name:
                continue
            return {
                "trip_name": f"{trip_date} {trip_event_name}",
                "trip_date": trip_date,
                "trip_event_name": trip_event_name,
                "trip_folder_name": candidate,
            }
        return {
            "trip_name": candidate,
            "trip_event_name": candidate,
            "trip_folder_name": candidate,
        }
    return {}


def caption_and_tags_from_path(path: Path, media_type: str) -> tuple[str, list[str], list[str]]:
    words = [word for word in path.stem.replace("-", " ").replace("_", " ").split() if word]
    base_tags = [word.lower() for word in words[:6]]
    objects = []
    if any(word.lower() in {"cat", "dog", "boat", "ridge", "beach", "mountain"} for word in words):
        objects = [word.lower() for word in words if word.lower() in {"cat", "dog", "boat", "ridge", "beach", "mountain"}]
    caption = f"{media_type.title()} captured at {' '.join(words) or path.name}."
    return caption, list(dict.fromkeys(base_tags)), list(dict.fromkeys(objects))
