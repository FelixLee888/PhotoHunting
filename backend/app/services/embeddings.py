from __future__ import annotations

import hashlib
import math
from collections.abc import Iterable

import httpx

from app.core.config import Settings


def cosine_similarity(left: Iterable[float], right: Iterable[float]) -> float:
    left_list = list(left)
    right_list = list(right)
    if not left_list or not right_list or len(left_list) != len(right_list):
        return 0.0
    numerator = sum(a * b for a, b in zip(left_list, right_list, strict=False))
    left_norm = math.sqrt(sum(a * a for a in left_list))
    right_norm = math.sqrt(sum(b * b for b in right_list))
    if not left_norm or not right_norm:
        return 0.0
    return numerator / (left_norm * right_norm)


class DeterministicEmbeddingProvider:
    def __init__(self, dimensions: int = 256) -> None:
        self.dimensions = dimensions

    def _embed(self, text: str) -> list[float]:
        tokens = [token.strip().lower() for token in text.split() if token.strip()]
        if not tokens:
            tokens = ["empty"]
        vector = [0.0] * self.dimensions
        for token in tokens:
            digest = hashlib.sha256(token.encode("utf-8")).digest()
            for index in range(self.dimensions):
                vector[index] += digest[index % len(digest)] / 255.0
        magnitude = math.sqrt(sum(value * value for value in vector)) or 1.0
        return [value / magnitude for value in vector]

    def embed_text(self, text: str) -> list[float]:
        return self._embed(text)

    def embed_document(self, text: str) -> list[float]:
        return self._embed(text)

    def embed_query(self, text: str) -> list[float]:
        return self._embed(text)


class GeminiEmbeddingProvider:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.fallback = DeterministicEmbeddingProvider(settings.embedding_dimensions)

    def embed_text(self, text: str) -> list[float]:
        return self.embed_document(text)

    def embed_document(self, text: str) -> list[float]:
        return self._embed(text, task_type="RETRIEVAL_DOCUMENT")

    def embed_query(self, text: str) -> list[float]:
        return self._embed(text, task_type="RETRIEVAL_QUERY")

    def _embed(self, text: str, *, task_type: str) -> list[float]:
        if not self.settings.gemini_api_key:
            return self.fallback.embed_document(text)

        headers = {"x-goog-api-key": self.settings.gemini_api_key}
        candidate_models = [self.settings.gemini_embedding_model]
        if self.settings.gemini_embedding_model != "gemini-embedding-001":
            candidate_models.append("gemini-embedding-001")

        for model_name in candidate_models:
            endpoint = self.settings.gemini_embedding_endpoint_template.format(model=model_name)
            payload = {
                "content": {"parts": [{"text": text}]},
                "taskType": task_type,
                "outputDimensionality": self.settings.embedding_dimensions,
            }
            try:
                response = httpx.post(endpoint, json=payload, headers=headers, timeout=20.0)
                response.raise_for_status()
                data = response.json()
                values = data.get("embedding", {}).get("values") or data.get("embeddings", [{}])[0].get("values")
                if isinstance(values, list) and values:
                    return [float(value) for value in values]
            except Exception:
                continue
        return self.fallback.embed_document(text)


def build_embedding_provider(settings: Settings) -> DeterministicEmbeddingProvider | GeminiEmbeddingProvider:
    if settings.embedding_provider.lower() == "gemini":
        return GeminiEmbeddingProvider(settings)
    return DeterministicEmbeddingProvider(settings.embedding_dimensions)
