"""Generate a few synthetic 'menu/poster' images so OCR has real text to extract.

Used only for the demo seed so users can immediately see the OCR pipeline at
work — picsum.photos images contain no text and would always OCR to empty.

Files are written into ``data/raw_seed/`` and served by FastAPI under
``/sampleimg/<filename>``. Generation is idempotent: if the file already exists
we skip it.
"""
from __future__ import annotations

from pathlib import Path

from ..config import settings


SAMPLES: dict[str, list[str]] = {
    "kyoto-menu.png": [
        "茶寮都路里  KYOTO  GION",
        "特选都路里圣代  ¥1430",
        "宇治金时刨冰    ¥1180",
        "抹茶白玉红豆    ¥980",
        "Open 10:30 - 19:00",
    ],
    "tokyo-cafe.png": [
        "GINZA SIX  6F TSUTAYA Coffee",
        "浅烘单品咖啡  ¥780",
        "Cheesecake Limited  ¥620",
        "Wi-Fi: TSUTAYA-COFFEE-G6",
    ],
    "la-venice.png": [
        "VENICE BEACH SIDEWALK CAFE",
        "Eggs Benedict  $18",
        "Acai Bowl     $14",
        "Open 8AM - 10PM Daily",
    ],
}


def _font(size: int):
    from PIL import ImageFont
    for fp in [
        "/System/Library/Fonts/PingFang.ttc",
        "/System/Library/Fonts/STHeiti Light.ttc",
        "/System/Library/Fonts/Helvetica.ttc",
        "/Library/Fonts/Arial Unicode.ttf",
    ]:
        try:
            return ImageFont.truetype(fp, size)
        except Exception:
            continue
    return ImageFont.load_default()


def _draw(path: Path, lines: list[str]) -> None:
    from PIL import Image, ImageDraw
    W, H = 800, 800
    img = Image.new("RGB", (W, H), (252, 248, 240))
    d = ImageDraw.Draw(img)
    title = lines[0]
    body = lines[1:]
    d.text((40, 60), title, fill=(40, 30, 30), font=_font(40))
    d.line([(40, 130), (W - 40, 130)], fill=(180, 60, 60), width=3)
    y = 180
    for line in body:
        d.text((50, y), line, fill=(60, 50, 50), font=_font(36))
        y += 70
    d.text((40, H - 60), "— sample image for OCR demo —", fill=(160, 150, 150), font=_font(20))
    img.save(path, "PNG")


def ensure_samples() -> Path:
    """Generate any missing sample images. Returns the directory."""
    out = Path(settings.data_dir).resolve() / "raw_seed"
    out.mkdir(parents=True, exist_ok=True)
    for name, lines in SAMPLES.items():
        p = out / name
        if not p.exists():
            try:
                _draw(p, lines)
            except Exception as exc:  # pragma: no cover
                print(f"[seed-images] failed to generate {name}: {exc}")
    return out


SAMPLE_URL_PREFIX = "http://localhost:8000/sampleimg"


def url_for(name: str) -> str:
    return f"{SAMPLE_URL_PREFIX}/{name}"
