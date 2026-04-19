from __future__ import annotations

import asyncio
import io
import json
import logging
import time
import uuid

import aiohttp
from aiogram import Bot
from aiogram.types import BufferedInputFile, InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.trends import trend_url_by_index, is_adult_trend
from app.db.models import Generation, User
from app.db.repo import add_balance, log_error
from app.services import kie
from app.services.http_session import make_tcp_connector
from app.services.photo import normalize_image_for_kie

logger = logging.getLogger(__name__)

PROGRESS_CHILD_1 = "Анализируем черты лица твоего малыша...\nОго, какие выразительные глазки! 😊✨"
PROGRESS_CHILD_2 = "Переносим движения из тренда...\nСкоро твой малыш станцует! 💃"
PROGRESS_CHILD_3 = "Дорабатываем последние детали...\nПочти готово! 🎬"

PROGRESS_ADULT_1 = "🎬 Изучаем ваш образ..."
PROGRESS_ADULT_2 = "💃 Переносим движения на персонажа..."
PROGRESS_ADULT_3 = "✨ Финальные штрихи..."

TIMEOUT_SEC = 20 * 60
POLL_INTERVAL = 5


async def run_single_generation(
    bot: Bot,
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    generation_id: int,
) -> None:
    async with session_factory() as session:
        gen = await session.get(Generation, generation_id)
        if not gen:
            return
        if gen.status != "pending":
            logger.info("run_single_generation: gen %s уже в статусе %s — пропуск", generation_id, gen.status)
            return
        uid = gen.user_id
        chat_id = gen.chat_id
        trend_idx = gen.trend_index
        file_id = gen.photo_file_id
        stored_url = gen.reference_video_url

        debit = await session.execute(
            update(User)
            .where(User.user_id == uid, User.balance >= 1)
            .values(balance=User.balance - 1)
        )
        if debit.rowcount != 1:
            await session.commit()
            try:
                await bot.send_message(chat_id, "Недостаточно генераций на балансе.")
            except Exception:
                logger.exception("send_message balance hint")
            return

        gen.status = "processing"
        await session.commit()

    video_ref_url = stored_url or trend_url_by_index(settings, trend_idx)

    try:
        file = await bot.get_file(file_id)
        buf = io.BytesIO()
        await bot.download_file(file.file_path, buf)
        raw = buf.getvalue()
    except Exception as e:
        await _fail(bot, session_factory, generation_id, uid, chat_id, f"download tg file: {e}")
        return

    try:
        raw, fname, ctype = normalize_image_for_kie(raw)
    except Exception as e:
        await _fail(bot, session_factory, generation_id, uid, chat_id, f"подготовка фото: {e}")
        return

    deadline = time.monotonic() + TIMEOUT_SEC

    _adult = is_adult_trend(trend_idx)
    p1 = PROGRESS_ADULT_1 if _adult else PROGRESS_CHILD_1
    p2 = PROGRESS_ADULT_2 if _adult else PROGRESS_CHILD_2
    p3 = PROGRESS_ADULT_3 if _adult else PROGRESS_CHILD_3

    async def progress_messages() -> None:
        await asyncio.sleep(120)
        try:
            await bot.send_message(chat_id, p1)
        except Exception:
            pass
        await asyncio.sleep(180)
        try:
            await bot.send_message(chat_id, p2)
        except Exception:
            pass
        await asyncio.sleep(120)
        try:
            await bot.send_message(chat_id, p3)
        except Exception:
            pass

    prog_task = asyncio.create_task(progress_messages())

    try:
        async with aiohttp.ClientSession(connector=make_tcp_connector(settings)) as http:
            video_ref = video_ref_url
            low = video_ref_url.lower()
            if "drive.google.com" in low or "docs.google.com" in low:
                try:
                    video_ref = await kie.upload_file_from_remote_url(
                        http,
                        settings,
                        video_ref_url,
                        file_name=f"trend_ref_{generation_id}_{uuid.uuid4().hex[:10]}.mp4",
                    )
                except kie.KieError as e:
                    await _fail(
                        bot,
                        session_factory,
                        generation_id,
                        uid,
                        chat_id,
                        "Референс-видео: нужна прямая ссылка на MP4. Google Drive часто отдаёт не файл, а страницу — "
                        f"Kie: {e.msg}",
                    )
                    return
            _stem, _dot, _ext = fname.rpartition(".")
            upload_name = (
                f"{_stem}_{generation_id}_{uuid.uuid4().hex[:12]}.{_ext}"
                if _dot
                else f"photo_{generation_id}_{uuid.uuid4().hex[:12]}.jpg"
            )
            img_url = await kie.upload_file_stream(http, settings, raw, upload_name, content_type=ctype)
            task_id = await kie.create_motion_task(http, settings, img_url, video_ref)
            async with session_factory() as session:
                gen = await session.get(Generation, generation_id)
                if gen:
                    gen.kie_task_id = task_id
                    await session.commit()

            while time.monotonic() < deadline:
                await asyncio.sleep(POLL_INTERVAL)
                info = await kie.get_task_info(http, settings, task_id)
                state = info.get("state")
                if state == "success":
                    url = kie.parse_result_video_url(info)
                    if not url:
                        raise kie.KieError(None, "Пустой resultJson")
                    await _deliver_video(bot, settings, session_factory, generation_id, uid, chat_id, url)
                    return
                if state == "fail":
                    msg = info.get("failMsg") or info.get("failCode") or "fail"
                    raise kie.KieError(None, str(msg))

            raise kie.KieError(None, "Генерация заняла слишком долго")
    except kie.KieError as e:
        await _fail(bot, session_factory, generation_id, uid, chat_id, e.msg)
    except Exception as e:
        await _fail(bot, session_factory, generation_id, uid, chat_id, str(e))
    finally:
        prog_task.cancel()
        try:
            await prog_task
        except asyncio.CancelledError:
            pass


async def _fail(
    bot: Bot,
    session_factory: async_sessionmaker[AsyncSession],
    generation_id: int,
    user_id: int,
    chat_id: int,
    err: str,
) -> None:
    async with session_factory() as session:
        gen = await session.get(Generation, generation_id)
        if gen:
            gen.status = "failed"
            gen.error_message = err[:2000]
        await add_balance(session, user_id, 1)
        await log_error(session, "kie", err, user_id, detail=json.dumps({"generation_id": generation_id}))
        await session.commit()
    hint = ""
    if "CERTIFICATE_VERIFY" in err or "SSL" in err:
        hint = (
            "\n\n(Причина: ошибка проверки SSL при связи с сервером Kie. "
            "Обнови зависимости: pip install -r requirements.txt и перезапусти бота. "
            "Временно для теста в .env: KIE_SSL_VERIFY=0 — только на свой риск.)"
        )
    elif "file format" in err.lower() or "format not support" in err.lower():
        hint = (
            "\n\n(Часто: фото не JPEG/PNG или ссылка на тренд — не прямой MP4. "
            "Проверь TREND_*_URL в настройках: лучше прямая ссылка на файл .mp4.)"
        )
    await bot.send_message(
        chat_id,
        "Не получилось сгенерировать видео 😔 Попробуй ещё раз или напиши в поддержку.\n"
        f"Детали: {err[:300]}"
        + hint,
    )


_MAX_TG_BYTES = 45 * 1024 * 1024  # 45 MB — лимит Telegram Bot API


async def _compress_video(data: bytes) -> bytes:
    """Сжимает видео через ffmpeg до битрейта 1500k. Возвращает исходные байты если ffmpeg не найден."""
    import shutil
    import tempfile

    if not shutil.which("ffmpeg"):
        logger.warning("ffmpeg не найден, сжатие пропущено")
        return data

    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as src_f:
        src_f.write(data)
        src_path = src_f.name

    dst_path = src_path.replace(".mp4", "_out.mp4")
    try:
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg", "-y", "-i", src_path,
            "-c:v", "libx264", "-b:v", "1500k",
            "-c:a", "aac", "-b:a", "128k",
            "-movflags", "+faststart",
            dst_path,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await asyncio.wait_for(proc.communicate(), timeout=300)
        if proc.returncode == 0:
            import os as _os
            compressed = _os.path.getsize(dst_path)
            logger.info("ffmpeg: %d → %d bytes", len(data), compressed)
            with open(dst_path, "rb") as f:
                return f.read()
        else:
            logger.warning("ffmpeg завершился с кодом %s, используем оригинал", proc.returncode)
            return data
    except Exception:
        logger.exception("ffmpeg compress")
        return data
    finally:
        import os as _os
        for p in (src_path, dst_path):
            try:
                _os.unlink(p)
            except FileNotFoundError:
                pass


async def _deliver_video(
    bot: Bot,
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    generation_id: int,
    user_id: int,
    chat_id: int,
    video_url: str,
) -> None:
    try:
        async with aiohttp.ClientSession(connector=make_tcp_connector(settings)) as http:
            async with http.get(video_url, timeout=aiohttp.ClientTimeout(total=300)) as resp:
                if resp.status != 200:
                    raise RuntimeError(f"скачивание видео: HTTP {resp.status}")
                data = await resp.read()
    except Exception as e:
        await _fail(bot, session_factory, generation_id, user_id, chat_id, str(e))
        return

    if len(data) < 512:
        await _fail(
            bot,
            session_factory,
            generation_id,
            user_id,
            chat_id,
            "результат слишком маленький — возможно ошибка на стороне Kie",
        )
        return

    # Сжимаем если файл больше лимита Telegram
    if len(data) > _MAX_TG_BYTES:
        logger.info("Видео %d bytes > 45MB, запускаем ffmpeg", len(data))
        data = await _compress_video(data)

    bot_mention = settings.bot_username or "бот"

    user_notified = False
    try:
        vid = BufferedInputFile(data, filename="dance.mp4")
        await bot.send_video(chat_id, vid, caption="🎉 Твоё видео готово!")
        user_notified = True
    except Exception:
        logger.exception("send_video chat_id=%s", chat_id)
        # Файл слишком большой даже после сжатия — отправляем как документ
        try:
            vid = BufferedInputFile(data, filename="dance.mp4")
            await bot.send_document(chat_id, vid, caption="🎉 Твоё видео готово!")
            user_notified = True
        except Exception:
            logger.exception("send_document chat_id=%s", chat_id)
            try:
                await bot.send_message(
                    chat_id,
                    "Видео готово — не удалось отправить файлом в Telegram. Скачай по ссылке:\n" + video_url[:2000],
                )
                user_notified = True
            except Exception:
                logger.exception("send_message fallback chat_id=%s", chat_id)

    if not user_notified:
        await _fail(
            bot,
            session_factory,
            generation_id,
            user_id,
            chat_id,
            "не удалось доставить видео в Telegram",
        )
        return

    async with session_factory() as session:
        gen = await session.get(Generation, generation_id)
        if gen:
            gen.status = "completed"
            gen.result_url = video_url
        r = await session.execute(select(User).where(User.user_id == user_id))
        u = r.scalar_one_or_none()
        if u:
            u.first_video_completed = True
            if not u.upsell_shown:
                u.upsell_eligible = True
        await session.commit()

    tags = "#babydance #trending #cutiebaby #viral #tiktokmom"
    kb = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="Создать новое видео", callback_data="new_video")]]
    )
    try:
        await bot.send_message(
            chat_id,
            "Что теперь?\n\n"
            "✅ Сохрани в галерею\n"
            "✅ Отправь близким (они офигеют! 😊)\n"
            "✅ Выложи в TikTok/Reels:\n\n"
            f"Лучшие хештеги для взрыва просмотров:\n{tags}\n\n"
            "📍 Добавь локацию (Москва/твой город)\n"
            "🎵 Укажи трек из видео (автоопределится)\n"
            f"📝 В описании: \"Сделано в @{bot_mention}\" (необязательно, но приятно 💙)\n\n"
            "🏆 КОНКУРС (скоро): Набери 1M просмотров с нашей ссылкой — получи iPhone!\n"
            "(детали скоро)\n\n"
            "Хочешь ещё видео? 🎬",
            reply_markup=kb,
        )
    except Exception:
        logger.exception("follow-up send_message chat_id=%s", chat_id)

    asyncio.create_task(_schedule_post_upsell(bot, session_factory, user_id, chat_id))


async def _schedule_post_upsell(
    bot: Bot,
    session_factory: async_sessionmaker[AsyncSession],
    user_id: int,
    chat_id: int,
) -> None:
    await asyncio.sleep(300)
    async with session_factory() as session:
        r = await session.execute(select(User).where(User.user_id == user_id))
        u = r.scalar_one_or_none()
        if not u or u.upsell_shown or not u.upsell_eligible:
            return

    try:
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="💳 Доплатить 150₽ → +2 видео", callback_data="upsell_pay")],
                [InlineKeyboardButton(text="🎬 Создать новое видео", callback_data="new_video")],
            ]
        )
        await bot.send_message(
            chat_id,
            "Понравилось? 😊\n\n"
            "🔥 СПЕЦ. ОФФЕР (действует 24 часа):\n"
            "Доплати 150₽ — получи ещё 2 видео!\n\n"
            "Экономия 149₽ по сравнению с обычной ценой!",
            reply_markup=kb,
        )
    except Exception:
        logger.exception("post_upsell send chat_id=%s", chat_id)
    async with session_factory() as session:
        r = await session.execute(select(User).where(User.user_id == user_id))
        u = r.scalar_one_or_none()
        if u:
            u.upsell_shown = True
            await session.commit()


def _log_generation_task(task: asyncio.Task) -> None:
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.error("run_single_generation упала: %s", exc, exc_info=exc)


def spawn_generation(
    bot: Bot,
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    generation_id: int,
) -> None:
    t = asyncio.create_task(run_single_generation(bot, settings, session_factory, generation_id))
    t.add_done_callback(_log_generation_task)
