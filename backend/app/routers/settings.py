from __future__ import annotations

from fastapi import APIRouter

from ..config import settings

router = APIRouter(prefix="/api/settings", tags=["settings"])


@router.get("")
def get_settings() -> dict:
    if settings.has_azure_openai:
        llm_provider = f"azure_openai · {settings.azure_openai_chat_deployment}"
        emb_provider = f"azure_openai · {settings.azure_openai_embedding_deployment}"
    elif settings.qwen_api_key:
        llm_provider = "qwen · qwen-plus"
        emb_provider = "qwen · text-embedding-v3"
    else:
        llm_provider = "mock"
        emb_provider = "mock"
    return {
        "providers": {
            "llm": llm_provider,
            "embedding": emb_provider,
            "amap": "configured" if settings.amap_api_key else "mock",
            "amap_js": "configured" if (settings.amap_js_api_key or settings.amap_api_key) else "mock",
            "google_places": "configured" if settings.google_places_api_key else "mock",
        },
        "maps": {
            "amap_js_api_key": settings.amap_js_api_key or settings.amap_api_key,
            "amap_security_js_code": settings.amap_security_js_code,
        },
        "data_dir": str(settings.data_path),
        "use_mock": settings.use_mock,
    }
