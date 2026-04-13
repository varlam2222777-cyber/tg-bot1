from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


def _env_bool(key: str, default: bool = True) -> bool:
    v = os.getenv(key)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


def _split_ids(raw: str | None) -> list[int]:
    if not raw:
        return []
    out: list[int] = []
    for part in raw.split(","):
        part = part.strip()
        if part.isdigit():
            out.append(int(part))
    return out


@dataclass(frozen=True)
class CategoryTrend:
    index: int        # уникальный: дети 1–99, взрослые 101–199
    name: str
    video_url: str
    promo_file_id: str  # file_id превью-видео в Telegram (может быть пустым)


@dataclass(frozen=True)
class Settings:
    telegram_token: str
    admin_ids: list[int]
    kie_api_key: str
    kie_model: str
    kie_callback_url: str
    kie_upload_base: str
    kie_api_base: str
    child_trends: list[CategoryTrend]
    adult_trends: list[CategoryTrend]
    yookassa_shop_id: str
    yookassa_secret_key: str
    yookassa_return_url: str
    policy_url: str
    support_username: str
    bot_username: str
    promo_video_file_id: str | None
    database_url: str
    kie_ssl_verify: bool
    example_video_1_file_id: str | None
    example_video_2_file_id: str | None
    example_video_3_file_id: str | None


def _load_category_trends(prefix: str, base_index: int) -> list[CategoryTrend]:
    """Читает CHILD_TREND_N_* или ADULT_TREND_N_* пока есть NAME."""
    trends: list[CategoryTrend] = []
    i = 1
    while True:
        name = os.getenv(f"{prefix}_TREND_{i}_NAME", "").strip()
        if not name:
            break
        url = os.getenv(f"{prefix}_TREND_{i}_URL", "").strip()
        promo = os.getenv(f"{prefix}_TREND_{i}_PROMO", "").strip()
        if url:
            trends.append(CategoryTrend(
                index=base_index + i - 1,
                name=name,
                video_url=url,
                promo_file_id=promo,
            ))
        i += 1
    return trends


def load_settings() -> Settings:
    child_trends = _load_category_trends("CHILD", base_index=1)
    adult_trends = _load_category_trends("ADULT", base_index=101)

    return Settings(
        telegram_token=os.getenv("TELEGRAM_TOKEN", "").strip(),
        admin_ids=_split_ids(os.getenv("ADMIN_IDS")) or [326666138],
        kie_api_key=os.getenv("KIE_API_KEY", "").strip(),
        kie_model=os.getenv("KIE_MODEL", "kling-2.6/motion-control").strip(),
        kie_callback_url=os.getenv("KIE_CALLBACK_URL", "https://httpbin.org/post").strip(),
        kie_upload_base=os.getenv("KIE_UPLOAD_BASE", "https://kieai.redpandaai.co").rstrip("/"),
        kie_api_base=os.getenv("KIE_API_BASE", "https://api.kie.ai").rstrip("/"),
        child_trends=child_trends,
        adult_trends=adult_trends,
        yookassa_shop_id=os.getenv("YOOKASSA_SHOP_ID", "").strip(),
        yookassa_secret_key=os.getenv("YOOKASSA_SECRET_KEY", "").strip(),
        yookassa_return_url=os.getenv("YOOKASSA_RETURN_URL", "https://t.me/").strip(),
        policy_url=os.getenv("POLICY_URL", "https://example.com/policy").strip(),
        support_username=os.getenv("SUPPORT_USERNAME", "support").strip().lstrip("@"),
        bot_username=os.getenv("BOT_USERNAME", "").strip().lstrip("@"),
        promo_video_file_id=os.getenv("PROMO_VIDEO_FILE_ID") or None,
        database_url=os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./bot.db").strip(),
        kie_ssl_verify=_env_bool("KIE_SSL_VERIFY", True),
        example_video_1_file_id=os.getenv("EXAMPLE_VIDEO_1_FILE_ID") or None,
        example_video_2_file_id=os.getenv("EXAMPLE_VIDEO_2_FILE_ID") or None,
        example_video_3_file_id=os.getenv("EXAMPLE_VIDEO_3_FILE_ID") or None,
    )


def yookassa_configured(settings: Settings) -> bool:
    return bool(settings.yookassa_shop_id and settings.yookassa_secret_key)
