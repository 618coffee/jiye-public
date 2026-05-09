"""Embedding provider — real Qwen text-embedding-v3 or a deterministic hash mock."""
from __future__ import annotations

import hashlib
import math
from abc import ABC, abstractmethod
from typing import Iterable

from ..config import settings


class EmbeddingProvider(ABC):
    dim: int = 256

    @abstractmethod
    def embed(self, texts: list[str]) -> list[list[float]]: ...


class MockEmbeddingProvider(EmbeddingProvider):
    """Hashed bag-of-tokens embedding.

    Quality is not great, but it's stable and lets the whole semantic-search
    pipeline run end-to-end with no API key.
    """

    dim = 256

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._embed_one(t) for t in texts]

    def _embed_one(self, text: str) -> list[float]:
        vec = [0.0] * self.dim
        # CJK-friendly: also split per-character + bigrams
        tokens: list[str] = []
        for line in text.split():
            tokens.append(line)
        for ch in text:
            if ch.strip():
                tokens.append(ch)
        for i in range(len(text) - 1):
            bg = text[i:i + 2].strip()
            if len(bg) == 2:
                tokens.append(bg)
        for tok in tokens:
            h = hashlib.sha1(tok.encode("utf-8")).digest()
            idx = int.from_bytes(h[:4], "big") % self.dim
            sign = 1.0 if h[4] & 1 else -1.0
            vec[idx] += sign
        # L2-normalize
        norm = math.sqrt(sum(x * x for x in vec)) or 1.0
        return [x / norm for x in vec]


class QwenEmbeddingProvider(EmbeddingProvider):
    dim = 1024

    def __init__(self, api_key: str):
        self.api_key = api_key

    def embed(self, texts: list[str]) -> list[list[float]]:
        import httpx
        url = "https://dashscope.aliyuncs.com/api/v1/services/embeddings/text-embedding/text-embedding"
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        body = {
            "model": "text-embedding-v3",
            "input": {"texts": texts},
            "parameters": {"dimension": self.dim, "output_type": "dense"},
        }
        resp = httpx.post(url, headers=headers, json=body, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        embs = data.get("output", {}).get("embeddings", [])
        return [e["embedding"] for e in embs]


class AzureOpenAIEmbeddingProvider(EmbeddingProvider):
    """Azure AI Foundry / Azure OpenAI embeddings.

    If the deployment doesn't exist or returns 4xx (e.g. quota exhausted, or
    user accidentally pointed at a chat router), permanently switch to the
    in-process MockEmbeddingProvider so that all chunks share one dim.
    """

    # text-embedding-3-small=1536, -3-large=3072, -ada-002=1536
    dim = 1536

    def __init__(self, endpoint: str, api_key: str, deployment: str, api_version: str):
        ep = endpoint.rstrip("/")
        if "/api/projects/" in ep:
            ep = ep.split("/api/projects/")[0]
        self.url = (
            f"{ep}/openai/deployments/{deployment}/embeddings"
            f"?api-version={api_version}"
        )
        self.api_key = api_key
        self._fallback: MockEmbeddingProvider | None = None

    def embed(self, texts: list[str]) -> list[list[float]]:
        if self._fallback is not None:
            return self._fallback.embed(texts)
        import httpx
        try:
            r = httpx.post(
                self.url,
                headers={"api-key": self.api_key, "Content-Type": "application/json"},
                json={"input": texts},
                timeout=30,
            )
            r.raise_for_status()
            data = r.json()["data"]
            vecs = [d["embedding"] for d in data]
            if vecs and len(vecs[0]) != self.dim:
                self.dim = len(vecs[0])
            return vecs
        except Exception as e:
            # Permanently downgrade to mock for this process.
            print(f"[embedding] Azure embedding failed ({type(e).__name__}: {e!s:.120}); "
                  f"falling back to MockEmbeddingProvider for the rest of this process.")
            self._fallback = MockEmbeddingProvider()
            self.dim = self._fallback.dim
            return self._fallback.embed(texts)


_singleton: EmbeddingProvider | None = None


def get_embedding_provider() -> EmbeddingProvider:
    global _singleton
    if _singleton is not None:
        return _singleton
    if settings.use_mock == "true":
        _singleton = MockEmbeddingProvider()
    elif settings.has_azure_openai and settings.azure_openai_embedding_deployment.strip() and settings.azure_openai_embedding_deployment.strip().lower() != "mock":
        _singleton = AzureOpenAIEmbeddingProvider(
            settings.azure_openai_endpoint,
            settings.azure_openai_api_key,
            settings.azure_openai_embedding_deployment,
            settings.azure_openai_api_version,
        )
    elif settings.qwen_api_key:
        _singleton = QwenEmbeddingProvider(settings.qwen_api_key)
    else:
        _singleton = MockEmbeddingProvider()
    return _singleton
