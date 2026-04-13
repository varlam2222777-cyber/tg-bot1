from __future__ import annotations

from app.config import CategoryTrend, Settings


def all_trends(settings: Settings) -> list[CategoryTrend]:
    return settings.child_trends + settings.adult_trends


def trend_by_index(settings: Settings, idx: int) -> CategoryTrend | None:
    for t in all_trends(settings):
        if t.index == idx:
            return t
    return None


def trend_url_by_index(settings: Settings, idx: int) -> str:
    t = trend_by_index(settings, idx)
    if t:
        return t.video_url
    raise ValueError(f"Нет тренда с индексом {idx}. Проверь CHILD_TREND_* / ADULT_TREND_* в .env.")


def is_adult_trend(idx: int) -> bool:
    return idx >= 100
