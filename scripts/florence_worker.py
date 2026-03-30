#!/usr/bin/env python3
from __future__ import annotations

import argparse
import copy
import logging
import socket
import sys
import time
from collections import Counter
from dataclasses import dataclass, field
from io import BytesIO
from typing import Any

import httpx
from PIL import Image, ImageFile, ImageOps, UnidentifiedImageError

try:
    from pillow_heif import register_heif_opener
except ImportError:  # pragma: no cover - optional image decoder
    register_heif_opener = None

try:
    import transformers
    import torch
    from transformers import AutoModelForCausalLM, AutoProcessor
except ImportError as exc:  # pragma: no cover - runtime dependency guard
    raise SystemExit(
        "Missing Florence worker dependencies. Install scripts/requirements-florence.txt first."
    ) from exc


DEFAULT_API_BASE_URL = "http://felix-ts-230:8000/api"
DEFAULT_MODEL_ID = "microsoft/Florence-2-base-ft"
DEFAULT_ANALYSIS_VERSION = "florence2-base-ft-v1"
DEFAULT_LIMIT = 2
DEFAULT_POLL_SECONDS = 12
DEFAULT_RETRY_COUNT = 3
DEFAULT_RETRY_DELAY_SECONDS = 5.0
TASK_DETAILED_CAPTION = "<DETAILED_CAPTION>"
TASK_MORE_DETAILED_CAPTION = "<MORE_DETAILED_CAPTION>"
TASK_OCR = "<OCR>"
TASK_OBJECT_DETECTION = "<OD>"
KEYWORD_STOP_WORDS = {
    "the",
    "and",
    "with",
    "from",
    "into",
    "over",
    "near",
    "while",
    "photo",
    "image",
    "scene",
    "shows",
    "showing",
    "visible",
    "there",
    "this",
    "that",
    "those",
    "these",
    "small",
    "large",
    "group",
    "taken",
    "capture",
    "captured",
    "standing",
    "sitting",
    "close",
    "view",
}
KEYWORD_ALLOWLIST = {
    "animal",
    "autumn",
    "beach",
    "bird",
    "boat",
    "bridge",
    "building",
    "cat",
    "city",
    "cliff",
    "coast",
    "dog",
    "field",
    "flower",
    "forest",
    "garden",
    "harbor",
    "hike",
    "hiking",
    "hill",
    "indoor",
    "island",
    "lake",
    "landscape",
    "market",
    "mountain",
    "museum",
    "night",
    "outdoor",
    "park",
    "path",
    "portrait",
    "ridge",
    "river",
    "road",
    "sailing",
    "sea",
    "shore",
    "sky",
    "snow",
    "street",
    "summit",
    "sunrise",
    "sunset",
    "temple",
    "town",
    "trail",
    "travel",
    "tree",
    "valley",
    "village",
    "water",
    "waterfall",
}
ENVIRONMENT_TAGS = {
    "beach",
    "city",
    "coast",
    "forest",
    "harbor",
    "hiking",
    "indoor",
    "island",
    "lake",
    "landscape",
    "mountain",
    "night",
    "outdoor",
    "ridge",
    "river",
    "sailing",
    "sea",
    "shore",
    "snow",
    "street",
    "sunrise",
    "sunset",
    "trail",
    "travel",
    "valley",
    "water",
}


@dataclass(slots=True)
class AnalysisJob:
    id: str
    filename: str
    source_path: str
    checksum: str
    stream_url: str
    thumbnail_url: str | None
    date_taken: str | None
    width: int | None
    height: int | None
    camera_model: str | None
    country: str | None
    region: str | None
    city: str | None
    place: str | None
    latitude: float | None
    longitude: float | None
    metadata_json: dict[str, Any] = field(default_factory=dict)
    analysis_status: str | None = None
    analysis_attempts: int = 0
    analysis_model: str | None = None
    analysis_version: str | None = None


class PhotoHuntingApiClient:
    def __init__(
        self,
        *,
        api_base_url: str,
        worker_id: str,
        analysis_model: str,
        analysis_version: str,
        timeout_seconds: float,
        request_retries: int,
        retry_delay_seconds: float,
    ) -> None:
        self.api_base_url = api_base_url.rstrip("/")
        self.worker_id = worker_id
        self.analysis_model = analysis_model
        self.analysis_version = analysis_version
        self.request_retries = max(request_retries, 1)
        self.retry_delay_seconds = max(retry_delay_seconds, 0.0)
        self.client = httpx.Client(
            timeout=httpx.Timeout(
                connect=min(timeout_seconds, 10.0),
                read=timeout_seconds,
                write=timeout_seconds,
                pool=timeout_seconds,
            )
        )

    def _request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        last_error: Exception | None = None
        for attempt in range(1, self.request_retries + 1):
            try:
                response = self.client.request(method, url, **kwargs)
                response.raise_for_status()
                return response
            except (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError) as exc:
                last_error = exc
                if attempt >= self.request_retries:
                    break
                logging.warning(
                    "%s %s failed on attempt %s/%s: %s",
                    method,
                    url,
                    attempt,
                    self.request_retries,
                    exc,
                )
                time.sleep(self.retry_delay_seconds)
        assert last_error is not None
        raise last_error

    def list_jobs(self, *, limit: int) -> list[AnalysisJob]:
        response = self._request(
            "GET",
            f"{self.api_base_url}/analysis/jobs",
            params={
                "status": "pending",
                "limit": limit,
                "analysis_model": self.analysis_model,
                "analysis_version": self.analysis_version,
            },
        )
        payload = response.json()
        return [AnalysisJob(**job) for job in payload.get("jobs", [])]

    def claim_job(self, media_id: str) -> AnalysisJob:
        response = self._request(
            "POST",
            f"{self.api_base_url}/analysis/jobs/{media_id}/claim",
            json={
                "worker_id": self.worker_id,
                "analysis_model": self.analysis_model,
                "analysis_version": self.analysis_version,
            },
        )
        return AnalysisJob(**response.json())

    def submit_completed(self, media_id: str, payload: dict[str, Any]) -> None:
        self._request(
            "POST",
            f"{self.api_base_url}/analysis/results/{media_id}",
            json={
                "worker_id": self.worker_id,
                "status": "completed",
                "analysis_model": self.analysis_model,
                "analysis_version": self.analysis_version,
                **payload,
            },
        )

    def submit_failed(self, media_id: str, error: str) -> None:
        self._request(
            "POST",
            f"{self.api_base_url}/analysis/results/{media_id}",
            json={
                "worker_id": self.worker_id,
                "status": "failed",
                "analysis_model": self.analysis_model,
                "analysis_version": self.analysis_version,
                "error": error[:1000],
            },
        )

    def heartbeat(self) -> None:
        self._request("POST", f"{self.api_base_url}/analysis/heartbeat/{self.worker_id}")

    def fetch_image(self, stream_url: str) -> Image.Image:
        response = self._request("GET", stream_url)
        try:
            with Image.open(BytesIO(response.content)) as image:
                image = ImageOps.exif_transpose(image)
                if image.mode != "RGB":
                    image = image.convert("RGB")
                return image.copy()
        except OSError as exc:
            if "truncated" not in str(exc).lower():
                raise RuntimeError("Unable to fully decode the streamed image.") from exc
            previous_setting = ImageFile.LOAD_TRUNCATED_IMAGES
            try:
                ImageFile.LOAD_TRUNCATED_IMAGES = True
                with Image.open(BytesIO(response.content)) as image:
                    image.load()
                    image = ImageOps.exif_transpose(image)
                    if image.mode != "RGB":
                        image = image.convert("RGB")
                    return image.copy()
            except UnidentifiedImageError as fallback_exc:
                raise RuntimeError(
                    "Unable to decode the streamed image. Install pillow-heif for HEIC/HEIF support if needed."
                ) from fallback_exc
            except OSError as fallback_exc:
                raise RuntimeError("Unable to decode the streamed image, even with truncated-image fallback.") from fallback_exc
            finally:
                ImageFile.LOAD_TRUNCATED_IMAGES = previous_setting
        except UnidentifiedImageError as exc:
            raise RuntimeError(
                "Unable to decode the streamed image. Install pillow-heif for HEIC/HEIF support if needed."
            ) from exc

    def close(self) -> None:
        self.client.close()


class FlorenceAnalyzer:
    def __init__(self, *, model_id: str, device: str) -> None:
        self.model_id = model_id
        self.device = device
        self.torch_dtype = torch.float16 if device == "cuda" else torch.float32
        self.processor = AutoProcessor.from_pretrained(model_id, trust_remote_code=True)
        self.model = AutoModelForCausalLM.from_pretrained(
            model_id,
            trust_remote_code=True,
            attn_implementation="eager",
            torch_dtype=self.torch_dtype,
        ).to(device)
        self.model.eval()
        self.generation_config = copy.deepcopy(self.model.generation_config)
        self.generation_config.num_beams = 1
        self.generation_config.early_stopping = False
        self.generation_config._from_model_config = False

    def analyze(self, job: AnalysisJob, image: Image.Image) -> dict[str, Any]:
        caption_ai = self._text_task(image, TASK_DETAILED_CAPTION, max_new_tokens=96)
        caption_dense = self._text_task(image, TASK_MORE_DETAILED_CAPTION, max_new_tokens=160)
        ocr_text = self._ocr_task(image)
        objects = self._object_detection_task(image)
        tags = build_tag_list(caption_ai, caption_dense, ocr_text, objects)
        landmarks = infer_landmarks(job, caption_ai, caption_dense, ocr_text)
        scene_tags = [tag for tag in tags if tag in ENVIRONMENT_TAGS]

        scene_json: dict[str, Any] = {
            "summary": caption_dense or caption_ai,
            "environment_tags": scene_tags,
            "source": "florence2-base-ft",
        }
        if landmarks:
            scene_json["place_hint"] = landmarks[0]
        elif job.place:
            scene_json["place_hint"] = job.place
        elif job.city:
            scene_json["place_hint"] = job.city

        return {
            "caption_ai": caption_ai,
            "caption_dense": caption_dense,
            "ocr_text": ocr_text,
            "objects_json": objects,
            "tags_json": tags,
            "landmarks_json": landmarks,
            "scene_json": scene_json,
        }

    def _text_task(self, image: Image.Image, task: str, *, max_new_tokens: int) -> str | None:
        payload = self._run_task(image, task, max_new_tokens=max_new_tokens)
        return clean_text(strings_from_payload(payload))

    def _ocr_task(self, image: Image.Image) -> str | None:
        payload = self._run_task(image, task=TASK_OCR, max_new_tokens=192)
        return clean_text(" ".join(strings_from_payload(payload).split()))

    def _object_detection_task(self, image: Image.Image) -> list[str]:
        payload = self._run_task(image, task=TASK_OBJECT_DETECTION, max_new_tokens=256)
        task_payload = extract_task_payload(payload, TASK_OBJECT_DETECTION)
        if isinstance(task_payload, dict):
            labels = task_payload.get("labels") or []
            return normalize_list(labels)
        return normalize_list(flatten_strings(task_payload))

    def _run_task(self, image: Image.Image, task: str, *, max_new_tokens: int) -> Any:
        inputs = self.processor(text=task, images=image, return_tensors="pt")
        generate_inputs: dict[str, Any] = {}
        for key, value in inputs.items():
            tensor = value.to(self.device)
            if key == "pixel_values":
                tensor = tensor.to(self.torch_dtype)
            generate_inputs[key] = tensor

        with torch.inference_mode():
            generated_ids = self.model.generate(
                input_ids=generate_inputs.get("input_ids"),
                pixel_values=generate_inputs.get("pixel_values"),
                attention_mask=generate_inputs.get("attention_mask"),
                generation_config=self.generation_config,
                max_new_tokens=max_new_tokens,
                do_sample=False,
                use_cache=False,
            )

        generated_text = self.processor.batch_decode(generated_ids, skip_special_tokens=False)[0]
        return self.processor.post_process_generation(
            generated_text,
            task=task,
            image_size=(image.width, image.height),
        )


def extract_task_payload(payload: Any, task: str) -> Any:
    if isinstance(payload, dict):
        if task in payload:
            return payload[task]
        if len(payload) == 1:
            return next(iter(payload.values()))
    return payload


def flatten_strings(payload: Any) -> list[str]:
    if isinstance(payload, str):
        cleaned = clean_text(payload)
        return [cleaned] if cleaned else []
    if isinstance(payload, dict):
        strings: list[str] = []
        for key, value in payload.items():
            if key in {"bboxes", "quad_boxes", "polygons"}:
                continue
            strings.extend(flatten_strings(value))
        return strings
    if isinstance(payload, list):
        strings: list[str] = []
        for value in payload:
            strings.extend(flatten_strings(value))
        return strings
    return []


def strings_from_payload(payload: Any) -> str:
    task_payload = payload
    if isinstance(payload, dict) and len(payload) == 1:
        task_payload = next(iter(payload.values()))
    return " ".join(flatten_strings(task_payload))


def clean_text(value: str | None) -> str | None:
    if not value:
        return None
    cleaned = " ".join(value.split()).strip()
    return cleaned or None


def normalize_list(values: list[str] | tuple[str, ...] | set[str] | None) -> list[str]:
    normalized: list[str] = []
    for value in values or []:
        if not isinstance(value, str):
            continue
        cleaned = value.strip().lower()
        if cleaned and cleaned not in normalized:
            normalized.append(cleaned)
    return normalized


def build_tag_list(caption_ai: str | None, caption_dense: str | None, ocr_text: str | None, objects: list[str]) -> list[str]:
    counts: Counter[str] = Counter()
    for value in [caption_ai or "", caption_dense or "", ocr_text or ""]:
        for raw_token in value.lower().replace("/", " ").split():
            token = "".join(character for character in raw_token if character.isalnum() or character == "-").strip("-")
            if len(token) < 3 or token in KEYWORD_STOP_WORDS:
                continue
            if token in KEYWORD_ALLOWLIST:
                counts[token] += 2
    for item in objects:
        if item:
            counts[item.lower()] += 3

    ordered = [token for token, _ in counts.most_common()]
    return ordered[:12]


def infer_landmarks(job: AnalysisJob, *texts: str | None) -> list[str]:
    combined = " ".join(text for text in texts if text).lower()
    candidates = [job.place, job.city, job.region, job.country]
    matches: list[str] = []
    for candidate in candidates:
        if not candidate:
            continue
        normalized = candidate.strip().lower()
        if normalized and normalized in combined and normalized not in matches:
            matches.append(normalized)
    return matches[:4]


def resolve_device(requested: str) -> str:
    if requested != "auto":
        return requested
    if torch.cuda.is_available():
        return "cuda"
    if getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
        return "mps"
    return "cpu"


def build_worker_id() -> str:
    return f"{socket.gethostname()}-florence"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the PhotoHunting Florence image analysis worker.")
    parser.add_argument("--api-base-url", default=DEFAULT_API_BASE_URL)
    parser.add_argument("--worker-id", default=build_worker_id())
    parser.add_argument("--model-id", default=DEFAULT_MODEL_ID)
    parser.add_argument("--analysis-version", default=DEFAULT_ANALYSIS_VERSION)
    parser.add_argument("--device", choices=["auto", "cpu", "mps", "cuda"], default="auto")
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    parser.add_argument("--poll-seconds", type=float, default=DEFAULT_POLL_SECONDS)
    parser.add_argument("--timeout-seconds", type=float, default=90.0)
    parser.add_argument("--request-retries", type=int, default=DEFAULT_RETRY_COUNT)
    parser.add_argument("--retry-delay-seconds", type=float, default=DEFAULT_RETRY_DELAY_SECONDS)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--log-level", default="INFO")
    return parser.parse_args()


def run_worker(args: argparse.Namespace) -> int:
    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(message)s",
    )
    logging.getLogger("transformers.generation.configuration_utils").setLevel(logging.ERROR)
    logging.getLogger("transformers.generation.utils").setLevel(logging.ERROR)

    major_version = int(transformers.__version__.split(".", maxsplit=1)[0])
    if major_version >= 5:
        raise SystemExit(
            "Florence-2 currently needs transformers 4.x in this worker setup. "
            f"Found transformers {transformers.__version__}. "
            "Run: pip install --upgrade --force-reinstall "
            "\"transformers>=4.49,<5\" \"tokenizers<0.22\""
        )

    device = resolve_device(args.device)
    if register_heif_opener is not None:
        register_heif_opener()
    logging.info("Loading Florence model %s on %s", args.model_id, device)
    analyzer = FlorenceAnalyzer(model_id=args.model_id, device=device)
    client = PhotoHuntingApiClient(
        api_base_url=args.api_base_url,
        worker_id=args.worker_id,
        analysis_model=args.model_id,
        analysis_version=args.analysis_version,
        timeout_seconds=args.timeout_seconds,
        request_retries=args.request_retries,
        retry_delay_seconds=args.retry_delay_seconds,
    )

    try:
        while True:
            try:
                client.heartbeat()
                jobs = client.list_jobs(limit=args.limit)
            except httpx.HTTPError as exc:
                logging.warning("Queue check failed: %s", exc)
                if args.once:
                    return 1
                time.sleep(args.poll_seconds)
                continue
            if not jobs:
                logging.info("No analysis jobs available.")
                if args.once:
                    return 0
                time.sleep(args.poll_seconds)
                continue

            claimed_any = False
            for job in jobs:
                try:
                    claimed_job = client.claim_job(job.id)
                except httpx.HTTPStatusError as exc:
                    if exc.response.status_code == 409:
                        continue
                    raise
                except httpx.HTTPError as exc:
                    logging.warning("Claim failed for %s: %s", job.id, exc)
                    continue

                claimed_any = True
                logging.info("Analyzing %s", claimed_job.source_path)
                try:
                    image = client.fetch_image(claimed_job.stream_url)
                    payload = analyzer.analyze(claimed_job, image)
                    client.submit_completed(claimed_job.id, payload)
                    logging.info("Completed %s", claimed_job.filename)
                except Exception as exc:  # pragma: no cover - worker runtime path
                    message = str(exc) or exc.__class__.__name__
                    logging.exception("Failed %s", claimed_job.filename)
                    try:
                        client.submit_failed(claimed_job.id, message)
                    except httpx.HTTPError as submit_exc:
                        logging.warning("Unable to submit failure for %s: %s", claimed_job.id, submit_exc)
                finally:
                    try:
                        client.heartbeat()
                    except httpx.HTTPError as exc:
                        logging.warning("Final heartbeat failed: %s", exc)

            if args.once:
                return 0
    finally:
        client.close()


if __name__ == "__main__":
    sys.exit(run_worker(parse_args()))
