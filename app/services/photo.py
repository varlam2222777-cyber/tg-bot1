from __future__ import annotations

import io
from dataclasses import dataclass

from PIL import Image


@dataclass
class PhotoValidation:
    ok: bool
    error_text: str | None = None


MIN_BYTES = 100 * 1024
MAX_BYTES = 10 * 1024 * 1024
MIN_SIDE = 300
MAX_SIDE = 8000


def validate_photo_bytes(data: bytes, filename_hint: str = "") -> PhotoValidation:
    if len(data) < MIN_BYTES:
        return PhotoValidation(False, "Файл слишком маленький (минимум ~100 KB).")
    if len(data) > MAX_BYTES:
        return PhotoValidation(False, "Файл больше 10 MB.")
    lower = filename_hint.lower()
    if lower and not (lower.endswith(".jpg") or lower.endswith(".jpeg") or lower.endswith(".png")):
        return PhotoValidation(False, "Нужен формат JPG или PNG.")
    try:
        with Image.open(io.BytesIO(data)) as im:
            im.verify()
        with Image.open(io.BytesIO(data)) as im:
            w, h = im.size
    except Exception:
        return PhotoValidation(False, "Не удалось прочитать изображение. Загрузи JPG или PNG.")
    if w < MIN_SIDE or h < MIN_SIDE:
        return PhotoValidation(False, f"Минимум {MIN_SIDE}×{MIN_SIDE} пикселей.")
    if w > MAX_SIDE or h > MAX_SIDE:
        return PhotoValidation(False, "Слишком большое разрешение.")
    return PhotoValidation(True, None)


def normalize_image_for_kie(data: bytes) -> tuple[bytes, str, str]:
    """Перекодирует байты в валидный JPEG или PNG с корректным именем и MIME для Kie."""
    im = Image.open(io.BytesIO(data))
    im.load()
    fmt = (im.format or "JPEG").upper()
    if fmt == "PNG":
        out = io.BytesIO()
        im.save(out, format="PNG", optimize=True)
        return out.getvalue(), "photo.png", "image/png"
    if im.mode != "RGB":
        im = im.convert("RGB")
    out = io.BytesIO()
    im.save(out, format="JPEG", quality=92, optimize=True)
    return out.getvalue(), "photo.jpg", "image/jpeg"
