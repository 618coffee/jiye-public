"""OCR + thumbnail caching for note images.

Strategy
--------
* Each image URL is downloaded once into ``data/raw/<note_id>_<idx>.<ext>``.
* A 320 px thumbnail is generated into ``data/thumb/<note_id>_<idx>.jpg``
  and served by the backend at ``/thumbs/<file>``.
* The image is OCR'd with RapidOCR (CN+EN, ~60MB ONNX models, CPU).
* Returned ``OCRResult`` includes per-image text + a thumbnail URL so the
  ingest pipeline can persist it as a ``chunk_type='ocr'`` row in
  ``note_chunks`` (auto-indexed by FTS + vector search).

The whole module is best-effort: if RapidOCR is missing or an HTTP fetch
fails, we just skip that image and return empty text — never blocking ingest.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence
import hashlib
import io
import threading

from ..config import settings


@dataclass
class OCRResult:
    image_idx: int
    source_url: str
    cached_path: Path | None = None
    thumb_path: Path | None = None
    thumb_url: str | None = None
    text: str = ""
    confidences: list[float] = field(default_factory=list)


_ocr_engine = None
_ocr_lock = threading.Lock()


def _get_engine():
    """Lazy-load RapidOCR (downloads ~60 MB models on first run)."""
    global _ocr_engine
    if _ocr_engine is not None:
        return _ocr_engine
    with _ocr_lock:
        if _ocr_engine is not None:
            return _ocr_engine
        try:
            from rapidocr_onnxruntime import RapidOCR
            _ocr_engine = RapidOCR()
        except Exception as exc:  # pragma: no cover
            print(f"[ocr] RapidOCR unavailable: {exc}; OCR disabled.")
            _ocr_engine = False
    return _ocr_engine


def _data_root() -> Path:
    return Path(settings.data_dir).resolve()


def _ensure_dirs() -> tuple[Path, Path]:
    raw_dir = _data_root() / "raw"
    thumb_dir = _data_root() / "thumb"
    raw_dir.mkdir(parents=True, exist_ok=True)
    thumb_dir.mkdir(parents=True, exist_ok=True)
    return raw_dir, thumb_dir


def _safe_filename(note_id: str, idx: int, suffix: str = ".jpg") -> str:
    safe = hashlib.sha1(f"{note_id}_{idx}".encode()).hexdigest()[:16]
    return f"{safe}{suffix}"


def _download(url: str, dest: Path, *, timeout: float = 15.0) -> bool:
    if dest.exists() and dest.stat().st_size > 0:
        return True
    # Special-case demo sample images that are served by this same FastAPI app
    # under /sampleimg/. They live on disk in data/raw_seed/ already; copying
    # avoids a chicken-and-egg HTTP fetch during the lifespan-time seed.
    if "/sampleimg/" in url:
        name = url.rsplit("/sampleimg/", 1)[-1]
        local = _data_root() / "raw_seed" / name
        if local.exists():
            dest.write_bytes(local.read_bytes())
            return True
    try:
        import httpx
        r = httpx.get(url, timeout=timeout, follow_redirects=True)
        r.raise_for_status()
        dest.write_bytes(r.content)
        return True
    except Exception as exc:  # pragma: no cover
        print(f"[ocr] download failed for {url}: {exc}")
        return False


def _make_thumb(src: Path, dst: Path, *, width: int = 320) -> bool:
    if dst.exists() and dst.stat().st_size > 0:
        return True
    try:
        from PIL import Image
        with Image.open(src) as im:
            im = im.convert("RGB")
            im.thumbnail((width, width * 4), Image.Resampling.LANCZOS)
            im.save(dst, "JPEG", quality=82, optimize=True)
        return True
    except Exception as exc:  # pragma: no cover
        print(f"[ocr] thumbnail failed for {src}: {exc}")
        return False


def _run_ocr(path: Path) -> tuple[str, list[float]]:
    engine = _get_engine()
    if not engine:
        return "", []
    try:
        result, _ = engine(str(path))
    except Exception as exc:  # pragma: no cover
        print(f"[ocr] ocr failed for {path}: {exc}")
        return "", []
    if not result:
        return "", []
    lines: list[str] = []
    confs: list[float] = []
    for row in result:
        # RapidOCR returns [box, text, confidence_str]
        if len(row) >= 3:
            text = (row[1] or "").strip()
            try:
                conf = float(row[2])
            except (TypeError, ValueError):
                conf = 0.0
            if text and conf >= 0.5:
                lines.append(text)
                confs.append(conf)
    return "\n".join(lines), confs


def process_images(
    note_id: str,
    image_urls: Sequence[str],
    *,
    enabled: bool = True,
) -> list[OCRResult]:
    """Download + thumbnail + OCR each image. Returns a result per URL."""
    out: list[OCRResult] = []
    if not image_urls:
        return out
    raw_dir, thumb_dir = _ensure_dirs()
    for idx, url in enumerate(image_urls):
        res = OCRResult(image_idx=idx, source_url=url)
        if not enabled:
            out.append(res)
            continue
        raw_name = _safe_filename(note_id, idx)
        raw_path = raw_dir / raw_name
        if not _download(url, raw_path):
            out.append(res)
            continue
        res.cached_path = raw_path
        thumb_name = _safe_filename(note_id, idx)
        thumb_path = thumb_dir / thumb_name
        if _make_thumb(raw_path, thumb_path):
            res.thumb_path = thumb_path
            res.thumb_url = f"/thumbs/{thumb_name}"
        text, confs = _run_ocr(raw_path)
        res.text = text
        res.confidences = confs
        out.append(res)
    return out
