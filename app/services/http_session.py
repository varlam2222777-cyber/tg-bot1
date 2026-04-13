"""SSL для aiohttp: на macOS часто нужен certifi, иначе CERTIFICATE_VERIFY_FAILED к внешним API."""

from __future__ import annotations

import logging
import ssl
from typing import TYPE_CHECKING

import aiohttp

if TYPE_CHECKING:
    from app.config import Settings

logger = logging.getLogger(__name__)


def make_tcp_connector(settings: "Settings") -> aiohttp.TCPConnector:
    if not settings.kie_ssl_verify:
        logger.warning("KIE_SSL_VERIFY=0: проверка SSL отключена (только для отладки)")
        return aiohttp.TCPConnector(ssl=False)
    try:
        import certifi

        ctx = ssl.create_default_context(cafile=certifi.where())
        return aiohttp.TCPConnector(ssl=ctx)
    except ImportError:
        logger.warning("Пакет certifi не установлен: pip install certifi")
        return aiohttp.TCPConnector()
    except Exception as e:
        logger.warning("Не удалось собрать SSL с certifi, системный контекст: %s", e)
        return aiohttp.TCPConnector()
