from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from app.states import GetFileId

router = Router(name="tools")


@router.message(Command("getfileid"))
async def cmd_getfileid(message: Message, state: FSMContext) -> None:
    await state.set_state(GetFileId.waiting)
    await message.answer(
        "Пришли видео сообщением (файл, кружок или документ с видео) — пришлю его <code>file_id</code>."
    )


@router.message(StateFilter(GetFileId.waiting), F.video)
async def getfileid_video(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(f"<code>{message.video.file_id}</code>")


@router.message(StateFilter(GetFileId.waiting), F.video_note)
async def getfileid_video_note(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(f"<code>{message.video_note.file_id}</code>")


@router.message(StateFilter(GetFileId.waiting), F.document)
async def getfileid_document(message: Message, state: FSMContext) -> None:
    doc = message.document
    if not doc or not doc.mime_type or not doc.mime_type.startswith("video/"):
        await message.answer("Нужен документ с типом video/… или пришли видео как видео, не как файл другого типа.")
        return
    await state.clear()
    await message.answer(f"<code>{doc.file_id}</code>")


@router.message(StateFilter(GetFileId.waiting))
async def getfileid_wrong(message: Message) -> None:
    await message.answer("Пришли видео (запись видео, кружок или видео как документ).")
