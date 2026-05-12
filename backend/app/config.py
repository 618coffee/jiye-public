"""Application config (env-driven)."""
from __future__ import annotations

import os
from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Storage
    data_dir: str = "../data"

    # Azure AI Foundry / Azure OpenAI (OpenAI-compatible)
    azure_openai_endpoint: str = ""
    azure_openai_api_key: str = ""
    azure_openai_chat_deployment: str = "gpt-4o-mini"
    azure_openai_embedding_deployment: str = "text-embedding-3-small"
    azure_openai_api_version: str = "2024-10-21"

    # Aliyun Dashscope (Qwen) — fallback if Azure not configured
    qwen_api_key: str = ""

    # POI validators
    amap_api_key: str = ""
    # Browser-visible AMap JS API key. Falls back to AMAP_API_KEY for local dev,
    # but production should use a web-js key with referer restrictions.
    amap_js_api_key: str = ""
    amap_security_js_code: str = ""
    google_places_api_key: str = ""

    # 'auto' = mock when key missing; 'true' = always mock; 'false' = never mock
    use_mock: str = "auto"

    # Image OCR + thumbnail pipeline
    ocr_enabled: bool = True
    thumbnail_width: int = 320

    # Embedding provider selection: "auto" prefers local e5-small if installed,
    # otherwise Azure → Qwen → Mock. "local" forces e5-small. "azure"/"qwen"/"mock"
    # force a specific provider.
    embedding_provider: str = "auto"    # Which Hugging Face model id to load when using the local provider.
    # Recommended choices:
    #   intfloat/multilingual-e5-small  — 120 MB, 384-d, fast on CPU/MPS
    #   intfloat/multilingual-e5-base   — 280 MB, 768-d, ~+5pp recall
    #   intfloat/multilingual-e5-large  — 560 MB, 1024-d, ~+8pp recall
    embedding_model_name: str = "intfloat/multilingual-e5-base"
    # Compute device: "auto" → MPS on Apple Silicon, CUDA on NVIDIA, else CPU.
    embedding_device: str = "auto"
    # Batch size used when encoding many chunks at once. MPS/CUDA win at ≥32;
    # CPU sweet spot is ~16-32. Larger uses more memory but is faster.
    embedding_batch_size: int = 64

    # XHS crawler defaults — can be overridden by env or per-call args.
    xhs_cdp_endpoint: str = "http://127.0.0.1:9222"
    xhs_collection_url: str = ""    # e.g. https://www.xiaohongshu.com/user/profile/<uid>?tab=fav&subTab=note

    @property
    def has_azure_openai(self) -> bool:
        return bool(self.azure_openai_endpoint and self.azure_openai_api_key)

    @property
    def has_llm(self) -> bool:
        return self.has_azure_openai or bool(self.qwen_api_key)

    @property
    def data_path(self) -> Path:
        p = Path(self.data_dir).resolve()
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def db_path(self) -> Path:
        return self.data_path / "index.db"

    def should_mock(self, key: str) -> bool:
        if self.use_mock == "true":
            return True
        if self.use_mock == "false":
            return False
        return not bool(key)


settings = Settings()
