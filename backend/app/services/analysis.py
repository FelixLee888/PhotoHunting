from __future__ import annotations

import base64
import json
import mimetypes
from io import BytesIO
from pathlib import Path
from typing import Any

import httpx
from PIL import Image, ImageOps

from app.core.config import Settings


class GeminiMediaAnalysisProvider:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def is_enabled(self) -> bool:
        return bool(self.settings.gemini_analysis_enabled and self.settings.gemini_api_key)

    def analyze_image(self, path: Path, metadata: dict[str, Any]) -> dict[str, Any]:
        if not self.is_enabled():
            return {}

        inline_data = self._prepare_image_inline_data(path)
        if not inline_data:
            return {}

        endpoint = self.settings.gemini_analysis_endpoint_template.format(model=self.settings.gemini_analysis_model)
        prompt = self._image_prompt(metadata)
        payload = {
            "systemInstruction": {
                "parts": [
                    {
                        "text": (
                            "You analyze personal photo library images. "
                            "Return compact factual JSON only. "
                            "Do not invent identities, locations, dates, or OCR text. "
                            "Use short natural captions and lowercase tags."
                        )
                    }
                ]
            },
            "contents": [{"parts": [{"text": prompt}, {"inlineData": inline_data}]}],
            "generationConfig": {"temperature": 0.1},
        }
        headers = {"x-goog-api-key": self.settings.gemini_api_key}

        try:
            response = httpx.post(
                endpoint,
                json=payload,
                headers=headers,
                timeout=self.settings.gemini_analysis_timeout_seconds,
            )
            response.raise_for_status()
            text = _response_text(response.json())
            if not text:
                return {}
            parsed = json.loads(text)
            return _normalize_analysis(parsed)
        except Exception:
            return {}

    def _prepare_image_inline_data(self, path: Path) -> dict[str, str] | None:
        try:
            with Image.open(path) as image:
                image = ImageOps.exif_transpose(image)
                image.thumbnail(
                    (
                        self.settings.gemini_analysis_max_dimension,
                        self.settings.gemini_analysis_max_dimension,
                    )
                )
                if image.mode in {"RGBA", "LA"}:
                    background = Image.new("RGB", image.size, (255, 255, 255))
                    alpha = image.getchannel("A") if "A" in image.getbands() else None
                    background.paste(image.convert("RGB"), mask=alpha)
                    image = background
                elif image.mode != "RGB":
                    image = image.convert("RGB")

                buffer = BytesIO()
                image.save(buffer, format="JPEG", quality=90, optimize=True)
                return {
                    "mimeType": "image/jpeg",
                    "data": base64.b64encode(buffer.getvalue()).decode("ascii"),
                }
        except Exception:
            try:
                raw_bytes = path.read_bytes()
            except Exception:
                return None
            if len(raw_bytes) > self.settings.gemini_analysis_max_inline_bytes:
                return None
            mime_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            return {
                "mimeType": mime_type,
                "data": base64.b64encode(raw_bytes).decode("ascii"),
            }

    @staticmethod
    def _image_prompt(metadata: dict[str, Any]) -> str:
        hints = []
        if metadata.get("camera_model"):
            hints.append(f"camera={metadata['camera_model']}")
        if metadata.get("date_taken"):
            hints.append(f"date_taken={metadata['date_taken']}")
        if metadata.get("latitude") is not None and metadata.get("longitude") is not None:
            hints.append("gps_present=true")
        hint_text = "; ".join(hints) if hints else "no extra hints"
        return (
            "Analyze this image for a local photo search index. "
            "Return JSON only with: "
            "caption (1 sentence), tags (up to 12), objects (up to 12), people (up to 8 visible non-identifying descriptors), "
            "activities (up to 8), landmark, place_hint, ocr_text, summary. "
            f"Context hints: {hint_text}."
        )


def build_media_analysis_provider(settings: Settings) -> GeminiMediaAnalysisProvider:
    return GeminiMediaAnalysisProvider(settings)


def _response_text(payload: dict[str, Any]) -> str | None:
    candidates = payload.get("candidates") or []
    if not candidates:
        return None
    parts = (candidates[0].get("content") or {}).get("parts") or []
    for part in parts:
        text = part.get("text")
        if isinstance(text, str) and text.strip():
            return _strip_code_fences(text.strip())
    return None


def _strip_code_fences(text: str) -> str:
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        return "\n".join(lines).strip()
    return text


def _normalize_analysis(payload: dict[str, Any]) -> dict[str, Any]:
    def normalize_list(key: str) -> list[str]:
        values = payload.get(key) or []
        if not isinstance(values, list):
            return []
        normalized: list[str] = []
        for value in values:
            if not isinstance(value, str):
                continue
            cleaned = value.strip().lower()
            if cleaned and cleaned not in normalized:
                normalized.append(cleaned)
        return normalized

    def normalize_text(key: str) -> str | None:
        value = payload.get(key)
        if not isinstance(value, str):
            return None
        cleaned = value.strip()
        return cleaned or None

    return {
        "caption": normalize_text("caption"),
        "tags": normalize_list("tags"),
        "objects": normalize_list("objects"),
        "people": normalize_list("people"),
        "activities": normalize_list("activities"),
        "landmark": normalize_text("landmark"),
        "place_hint": normalize_text("place_hint"),
        "ocr_text": normalize_text("ocr_text"),
        "summary": normalize_text("summary"),
    }
