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
    google_places_api_key: str = ""

    # 'auto' = mock when key missing; 'true' = always mock; 'false' = never mock
    use_mock: str = "auto"

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
