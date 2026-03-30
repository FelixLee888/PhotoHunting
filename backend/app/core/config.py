from __future__ import annotations

import json
from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "Photo Hunting API"
    api_prefix: str = "/api"
    database_url: str = "sqlite:///./data/photohunting.db"
    backend_cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:3000"])
    media_roots: list[str] = Field(default_factory=list)
    qdrant_url: str = "http://localhost:6333"
    qdrant_collection_prefix: str = "photohunting"
    embedding_provider: str = "deterministic"
    static_frontend_dir: str | None = None
    gemini_api_key: str | None = None
    gemini_embedding_model: str = "gemini-embedding-2"
    gemini_embedding_endpoint_template: str = (
        "https://generativelanguage.googleapis.com/v1beta/models/{model}:embedContent"
    )
    gemini_analysis_enabled: bool = False
    gemini_analysis_model: str = "gemini-2.5-flash"
    gemini_analysis_endpoint_template: str = (
        "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    )
    gemini_analysis_max_dimension: int = 1600
    gemini_analysis_max_inline_bytes: int = 4_000_000
    gemini_analysis_timeout_seconds: float = 45.0
    embedding_dimensions: int = 256
    analysis_claim_stale_minutes: int = 20
    preview_cache_dir: str = "./data/previews"
    preview_max_dimension: int = 480
    preview_jpeg_quality: int = 72
    seed_demo_data: bool = True

    @field_validator("backend_cors_origins", "media_roots", mode="before")
    @classmethod
    def split_list_settings(cls, value):
        if value is None or value == "":
            return []
        if isinstance(value, list):
            return value
        if isinstance(value, str):
            stripped = value.strip()
            if stripped.startswith("["):
                try:
                    parsed = json.loads(stripped)
                    if isinstance(parsed, list):
                        return parsed
                except json.JSONDecodeError:
                    pass
            return [item.strip() for item in stripped.split(",") if item.strip()]
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()
