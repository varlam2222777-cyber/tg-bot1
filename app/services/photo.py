from __future__ import annotations

import io
import logging
from dataclasses import dataclass

from PIL import Image

try:
    from pillow_heif import register_heif_opener
    register_heif_opener()
except ImportError:
    pass  # HEIC не поддерживается если pillow-heif не установлен

logger = logging.getLogger(__name__)


@dataclass
class PhotoValidation:
    ok: bool
    error_text: str | None = None


MIN_BYTES = 1024            # 1 KB — только совсем пустые файлы
MAX_BYTES = 50 * 1024 * 1024  # 50 MB — примем и сожмём если надо
MIN_SIDE = 200
MAX_SIDE = 8000
AUTO_COMPRESS_THRESHOLD = 10 * 1024 * 1024  # 10 MB — автосжатие


def validate_photo_bytes(data: bytes, filename_hint: str = "") -> PhotoValidation:
    size_mb = len(data) / (1024 * 1024)
    logger.info(
        "photo validation: size=%.2f MB, filename_hint=%r",
        size_mb, filename_hint,
    )

    if len(data) < MIN_BYTES:
        logger.warning("REJECT: too small %.2f MB (min %d bytes)", size_mb, MIN_BYTES)
        return PhotoValidation(False, f"Файл слишком маленький ({size_mb:.2f} MB, минимум ~10 KB).")

    if len(data) > MAX_BYTES:
        logger.warning("REJECT: too large %.2f MB (max %d MB)", size_mb, MAX_BYTES // (1024 * 1024))
        return PhotoValidation(False, f"Файл слишком большой ({size_mb:.1f} MB, максимум 50 MB).")

    # Не проверяем расширение — принимаем любые изображения, Pillow разберётся
    try:
        with Image.open(io.BytesIO(data)) as im:
            fmt = (im.format or "unknown").upper()
            im.verify()
        with Image.open(io.BytesIO(data)) as im:
            w, h = im.size
    except Exception as e:
        logger.warning("REJECT: cannot open image: %s", e)
        return PhotoValidation(False, "Не удалось прочитать изображение. Загрузи JPG или PNG.")

    logger.info("photo validation: format=%s, resolution=%dx%d", fmt, w, h)

    if w < MIN_SIDE or h < MIN_SIDE:
        logger.warning("REJECT: too small resolution %dx%d (min %d)", w, h, MIN_SIDE)
        return PhotoValidation(False, f"Слишком маленькое разрешение ({w}×{h}, минимум {MIN_SIDE}×{MIN_SIDE}).")

    if w > MAX_SIDE or h > MAX_SIDE:
        logger.warning("REJECT: too large resolution %dx%d (max %d)", w, h, MAX_SIDE)
        return PhotoValidation(False, f"Слишком большое разрешение ({w}×{h}).")

    logger.info("photo validation: ACCEPTED format=%s %dx%d %.2f MB", fmt, w, h, size_mb)
    return PhotoValidation(True, None)


def normalize_image_for_kie(data: bytes) -> tuple[bytes, str, str]:
    """Конвертирует в JPEG/PNG для Kie. Поддерживает HEIC, WebP, и автосжатие > 10 MB."""
    im = Image.open(io.BytesIO(data))
    im.load()
    fmt = (im.format or "").upper()

    w, h = im.size
    logger.info("normalize: input format=%s %dx%d (%.2f ratio) size=%d bytes mode=%s", fmt, w, h, w/h if h else 0, len(data), im.mode)

    # Любой формат кроме PNG → конвертируем в JPEG
    if fmt == "PNG":
        out = io.BytesIO()
        im.save(out, format="PNG", optimize=True)
        result = out.getvalue()
        if len(result) > AUTO_COMPRESS_THRESHOLD:
            logger.info("normalize: PNG too large (%d bytes), converting to JPEG", len(result))
            if im.mode in ("RGBA", "P", "LA"):
                im = im.convert("RGB")
            out = io.BytesIO()
            im.save(out, format="JPEG", quality=85, optimize=True)
            result = out.getvalue()
        logger.info("normalize: output PNG/JPEG %d bytes", len(result))
        if result[0:3] == b'\xff\xd8\xff':
            return result, "photo.jpg", "image/jpeg"
        return result, "photo.png", "image/png"

    # HEIC, WebP, BMP, TIFF, и т.д. → JPEG
    if im.mode != "RGB":
        im = im.convert("RGB")

    # Сначала пробуем quality=92
    quality = 92
    out = io.BytesIO()
    im.save(out, format="JPEG", quality=quality, optimize=True)
    result = out.getvalue()

    # Если > 10 MB — снижаем качество
    if len(result) > AUTO_COMPRESS_THRESHOLD:
        for q in (85, 75, 65):
            logger.info("normalize: JPEG %d bytes > 10MB, retrying quality=%d", len(result), q)
            out = io.BytesIO()
            im.save(out, format="JPEG", quality=q, optimize=True)
            result = out.getvalue()
            if len(result) <= AUTO_COMPRESS_THRESHOLD:
                break

    # Логируем финальное разрешение
    with Image.open(io.BytesIO(result)) as check:
        fw, fh = check.size
    logger.info("normalize: output JPEG %dx%d (%.2f ratio) %d bytes", fw, fh, fw/fh if fh else 0, len(result))
    return result, "photo.jpg", "image/jpeg"
