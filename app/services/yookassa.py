from __future__ import annotations

import asyncio
import logging
import uuid
from decimal import Decimal

from app.config import Settings, yookassa_configured

logger = logging.getLogger(__name__)


def _configure_sdk(settings: Settings) -> None:
    from yookassa import Configuration

    Configuration.account_id = settings.yookassa_shop_id
    Configuration.secret_key = settings.yookassa_secret_key


async def create_payment_url(
    settings: Settings,
    *,
    amount_rub: int,
    description: str,
    metadata: dict,
) -> tuple[str, str]:
    if not yookassa_configured(settings):
        raise RuntimeError("YooKassa не настроена")

    def _sync() -> tuple[str, str]:
        from yookassa import Payment

        _configure_sdk(settings)
        idempotence_key = str(uuid.uuid4())
        pay = Payment.create(
            {
                "amount": {"value": f"{Decimal(amount_rub):.2f}", "currency": "RUB"},
                "confirmation": {
                    "type": "redirect",
                    "return_url": settings.yookassa_return_url,
                },
                "capture": True,
                "description": description[:128],
                "metadata": {str(k): str(v) for k, v in metadata.items()},
            },
            idempotence_key,
        )
        url = pay.confirmation.confirmation_url
        pid = pay.id
        return url, pid

    return await asyncio.to_thread(_sync)


async def create_payment_url_safe(
    settings: Settings,
    *,
    amount_rub: int,
    description: str,
    metadata: dict,
) -> tuple[str, str] | tuple[None, None]:
    """Как create_payment_url, но не бросает исключение наружу (ошибка SDK/сети)."""
    try:
        return await create_payment_url(
            settings,
            amount_rub=amount_rub,
            description=description,
            metadata=metadata,
        )
    except Exception:
        logger.exception("YooKassa Payment.create")
        return None, None


async def get_payment_status(settings: Settings, payment_id: str) -> str | None:
    if not yookassa_configured(settings):
        return None

    def _sync() -> str:
        from yookassa import Payment

        _configure_sdk(settings)
        pay = Payment.find_one(payment_id)
        return pay.status

    try:
        return await asyncio.to_thread(_sync)
    except Exception:
        logger.exception("YooKassa Payment.find_one(%s)", payment_id)
        return None
