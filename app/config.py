from __future__ import annotations

import json
import os
from copy import deepcopy
from pathlib import Path
from typing import Any

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # OpenRouter credentials used when forwarding completions.
    openrouter_api_key: str = Field(default="", alias="OPENROUTER_API_KEY")

    # Postgres connection. DATABASE_URL overrides the individual fields.
    postgres_host: str = Field(default="postgres", alias="POSTGRES_HOST")
    postgres_port: int = Field(default=5432, alias="POSTGRES_PORT")
    postgres_user: str = Field(default="postgres", alias="POSTGRES_USER")
    postgres_password: str = Field(default="postgres", alias="POSTGRES_PASSWORD")
    postgres_db: str = Field(default="jev_router", alias="POSTGRES_DB")

    # Router API keys accepted from Hermes. Format:
    #   caller_id_1:key_1;caller_id_2:key_2
    router_api_keys: str = Field(default="", alias="ROUTER_API_KEYS")

    # Decision layer.
    jev_url: str = Field(default="https://openrouter.ai/api/alpha/decisions", alias="JEV_URL")
    jev_model: str = Field(default="typesafe/jev-1.13", alias="JEV_MODEL")
    jev_confidence_gap: float = Field(default=0.15, alias="JEV_CONFIDENCE_GAP")

    # Privacy.
    prompt_preview_default: bool = Field(default=False, alias="PROMPT_PREVIEW_DEFAULT")
    prompt_preview_length: int = Field(default=200, alias="PROMPT_PREVIEW_LENGTH")

    # Fallback classifier heuristic.
    simple_word_threshold: int = Field(default=25, alias="SIMPLE_WORD_THRESHOLD")
    complex_word_threshold: int = Field(default=100, alias="COMPLEX_WORD_THRESHOLD")
    complex_keywords: str = Field(
        default="refactor,multi-file,complex,architecture,design,debug,compare,analyze in detail,long context",
        alias="COMPLEX_KEYWORDS",
    )
    simple_keywords: str = Field(
        default="what is,how many,define,short,quick,brief",
        alias="SIMPLE_KEYWORDS",
    )

    @field_validator("complex_keywords", "simple_keywords", mode="before")
    @classmethod
    def _join_keywords(cls, v: Any) -> str:
        if isinstance(v, list):
            return ",".join(v)
        return str(v)

    @property
    def database_url(self) -> str:
        override = os.getenv("DATABASE_URL")
        if override:
            return override
        return (
            f"postgresql://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def complex_kw_list(self) -> list[str]:
        return [k.strip().lower() for k in self.complex_keywords.split(",") if k.strip()]

    @property
    def simple_kw_list(self) -> list[str]:
        return [k.strip().lower() for k in self.simple_keywords.split(",") if k.strip()]


SETTINGS = Settings()
PROJECT_ROOT = Path(__file__).resolve().parent.parent
TIERS_PATH = PROJECT_ROOT / "config" / "tiers.json"

_tiers_cache: dict[str, Any] | None = None
_tiers_cache_mtime: float | None = None
_tiers_cache_path: Path | None = None


def load_tiers(path: Path | None = None) -> dict[str, Any]:
    """Load tiers.json, cached by mtime.

    The router calls this on every single request (to resolve tier -> model),
    so a plain re-read + json.loads() on every call is wasted disk I/O once
    the file stops changing. os.stat() is orders of magnitude cheaper than a
    full read+parse, so we use it as a cheap freshness check: if the file's
    mtime hasn't moved since our last read, hand back a deep copy of the
    cached dict instead of touching disk again. A save via tiers_store.save_tiers()
    (atomic tmp + os.replace) always changes mtime, so admin edits still take
    effect on the very next request — no explicit cache invalidation needed.
    """
    global _tiers_cache, _tiers_cache_mtime, _tiers_cache_path
    path = path or TIERS_PATH
    if not path.exists():
        raise FileNotFoundError(f"Missing tiers config: {path}")

    mtime = path.stat().st_mtime
    if (
        _tiers_cache is not None
        and _tiers_cache_path == path
        and _tiers_cache_mtime == mtime
    ):
        return deepcopy(_tiers_cache)

    data = json.loads(path.read_text())
    _tiers_cache = data
    _tiers_cache_mtime = mtime
    _tiers_cache_path = path
    return deepcopy(data)


def min_context_length(tiers: dict[str, Any] | None = None) -> int:
    tiers = tiers or load_tiers()
    return min(t["context_length"] for t in tiers.values())


def tier_to_model(tier: str, tiers: dict[str, Any] | None = None) -> str:
    tiers = tiers or load_tiers()
    if tier not in tiers:
        tier = "medium"
    return tiers[tier]["model"]
