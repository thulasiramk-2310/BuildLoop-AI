"""Environment configuration. Secrets live only in the backend process."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", extra="ignore", protected_namespaces=())

    MODEL_API_KEY: str = ""
    META_BASE_URL: str = "https://api.meta.ai/v1"
    # Alternative reasoning backend: Muse Spark OPEN (open-weight) served by an inference partner through an
    # OpenAI-compatible Chat Completions endpoint. It is a different model from the Meta Model API and is labelled so.
    REASONING_BACKEND: str = "meta_api"  # meta_api | open_weight
    # Perception: "sam" = SAM 3.1 via Meta Model API (masks + boxes). "vlm" = whole-frame inventory from the open-weight
    # multimodal model (model-estimated boxes, NO masks, NOT SAM). Explicit opt-in; never an automatic fallback.
    PERCEPTION_BACKEND: str = "sam"  # sam | vlm
    # Open-weight provider presets. "auto": baseten if BASETEN_API_KEY is set, else huggingface if HF_TOKEN is set, else custom.
    OPEN_WEIGHT_PROVIDER: str = "auto"  # auto | huggingface | baseten | custom
    BASETEN_API_KEY: str = ""  # Baseten Model APIs, https://inference.baseten.co/v1 (OpenAI-compatible)
    HF_TOKEN: str = ""  # Hugging Face token (Inference Providers). Used as the open-weight key when OPEN_WEIGHT_API_KEY is blank.
    OPEN_WEIGHT_BASE_URL: str = ""
    OPEN_WEIGHT_API_KEY: str = ""
    OPEN_WEIGHT_MODEL: str = ""
    # Optional USD per 1M tokens, copied from the provider price list. Cost is only ever ESTIMATED from these.
    OPEN_WEIGHT_PRICE_IN_PER_M: float | None = None
    OPEN_WEIGHT_PRICE_OUT_PER_M: float | None = None
    META_REASONING_MODEL: str = "muse-spark-1.3"
    META_SEGMENTATION_MODEL: str = "sam-3.1"

    DATABASE_URL: str = f"sqlite:///{ROOT / 'buildloop.db'}"
    META_MAX_REQUESTS: int = 200  # hard budget per process
    META_MIN_INTERVAL_S: float = 1.0
    META_TIMEOUT_S: float = 60.0
    META_RETRIES: int = 3
    CORS_ORIGINS: str = "http://localhost:5173,https://localhost:5173"

    @field_validator("OPEN_WEIGHT_PRICE_IN_PER_M", "OPEN_WEIGHT_PRICE_OUT_PER_M", mode="before")
    @classmethod
    def _blank_price_is_none(cls, v):
        """`.env.example` ships these blank; a copied .env must not crash startup."""
        return None if isinstance(v, str) and not v.strip() else v

    @property
    def ow_provider(self) -> str:
        if self.OPEN_WEIGHT_PROVIDER != "auto":
            return self.OPEN_WEIGHT_PROVIDER
        if self.BASETEN_API_KEY.strip():
            return "baseten"
        return "huggingface" if self.HF_TOKEN.strip() else "custom"

    # Preset values. Baseten default slug = the hackathon participant guide's identifier; Baseten's general
    # docs use meta-models/muse-spark-open. Neither is confirmed against an account catalog yet: run scripts/baseten_audit.py and
    # override with OPEN_WEIGHT_MODEL if the catalog differs.
    _PRESETS = {
        "baseten": ("https://inference.baseten.co/v1", "meta-models/muse-spark-open-1026"),
        "huggingface": ("https://router.huggingface.co/v1", "meta-models/Muse-Glimmer-30B"),
    }

    @property
    def ow_key(self) -> str:
        if self.OPEN_WEIGHT_API_KEY.strip():
            return self.OPEN_WEIGHT_API_KEY.strip()
        return (self.BASETEN_API_KEY if self.ow_provider == "baseten" else self.HF_TOKEN).strip()

    @property
    def ow_base_url(self) -> str:
        return self.OPEN_WEIGHT_BASE_URL.strip() or self._PRESETS.get(self.ow_provider, ("", ""))[0]

    @property
    def ow_model(self) -> str:
        return self.OPEN_WEIGHT_MODEL.strip() or self._PRESETS.get(self.ow_provider, ("", ""))[1]

    @property
    def open_weight_configured(self) -> bool:
        return bool(self.ow_base_url and self.ow_key and self.ow_model)

    @property
    def meta_configured(self) -> bool:
        return bool(self.MODEL_API_KEY.strip())


@lru_cache
def get_settings() -> Settings:
    return Settings()
