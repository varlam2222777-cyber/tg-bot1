from __future__ import annotations

import json
import logging
from typing import Any

import aiohttp

from app.config import Settings

logger = logging.getLogger(__name__)


async def _response_json_or_error(resp: aiohttp.ClientResponse, ctx: str) -> dict[str, Any]:
    if resp.status >= 400:
        t = await resp.text()
        raise KieError(None, f"{ctx}: HTTP {resp.status} {t[:400]}")
    try:
        body = await resp.json(content_type=None)
    except Exception as e:
        raise KieError(None, f"{ctx}: ответ не JSON ({e})") from e
    if not isinstance(body, dict):
        raise KieError(None, f"{ctx}: ожидался JSON-объект, получено {type(body)}")
    return body


class KieError(Exception):
    def __init__(self, code: int | None, msg: str):
        self.code = code
        self.msg = msg
        super().__init__(f"KieError {code}: {msg}")


async def upload_file_stream(
    session: aiohttp.ClientSession,
    settings: Settings,
    file_bytes: bytes,
    filename: str,
    upload_path: str = "tg-bot",
    content_type: str = "application/octet-stream",
) -> str:
    url = f"{settings.kie_upload_base}/api/file-stream-upload"
    data = aiohttp.FormData()
    data.add_field("file", file_bytes, filename=filename, content_type=content_type)
    data.add_field("uploadPath", upload_path)
    data.add_field("fileName", filename)
    headers = {"Authorization": f"Bearer {settings.kie_api_key}"}
    async with session.post(url, data=data, headers=headers, timeout=aiohttp.ClientTimeout(total=120)) as resp:
        body = await _response_json_or_error(resp, "file-stream-upload")
    if not body.get("success") and body.get("code") != 200:
        raise KieError(body.get("code"), body.get("msg", str(body)))
    data_obj = body.get("data") or {}
    file_url = data_obj.get("fileUrl") or data_obj.get("downloadUrl")
    if not file_url:
        raise KieError(None, f"No fileUrl in upload response: {body}")
    return str(file_url)


async def upload_file_from_remote_url(
    session: aiohttp.ClientSession,
    settings: Settings,
    file_url: str,
    *,
    upload_path: str = "trends",
    file_name: str = "reference.mp4",
) -> str:
    """Скачивает файл по URL на стороне Kie и отдаёт прямую ссылку (обходит не-MP4 ссылки вроде Google Drive)."""
    url = f"{settings.kie_upload_base}/api/file-url-upload"
    headers = {
        "Authorization": f"Bearer {settings.kie_api_key}",
        "Content-Type": "application/json",
    }
    payload = {"fileUrl": file_url, "uploadPath": upload_path, "fileName": file_name}
    async with session.post(url, json=payload, headers=headers, timeout=aiohttp.ClientTimeout(total=300)) as resp:
        body = await _response_json_or_error(resp, "file-url-upload")
    if not body.get("success") and body.get("code") != 200:
        raise KieError(body.get("code"), body.get("msg", str(body)))
    data_obj = body.get("data") or {}
    out = data_obj.get("fileUrl") or data_obj.get("downloadUrl")
    if not out:
        raise KieError(None, f"No fileUrl in url-upload response: {body}")
    return str(out)


POSITIVE_PROMPT = (
    "Animate only the subject(s) present in the source image. "
    "Preserve exact identity, face, clothing, body proportions and body count from the photo. "
    "Do not add new people, characters, animals, or objects. "
    "Static background, fixed camera, clean composition. "
    "Photorealistic, natural lighting, high detail, cinematic quality, smooth realistic motion, "
    "natural physics, grounded footwork, stable limbs."
)

NEGATIVE_PROMPT = (
    "extra people, additional subjects, new characters, background people, crowd, "
    "duplicate subjects, cloned figures, twins, split screen, multiple versions of the same person, "
    "hallucinated objects, added props, mirrors with reflections, "
    "cartoon, anime, plastic skin, distorted anatomy, melted face, warped limbs, extra fingers, "
    "floating body parts, sliding feet, jitter, low quality, blurry, watermark, text"
)


async def create_motion_task(
    session: aiohttp.ClientSession,
    settings: Settings,
    image_url: str,
    video_url: str,
    prompt: str = "",
) -> str:
    full_prompt = POSITIVE_PROMPT + (f" {prompt}" if prompt.strip() else "")

    url = f"{settings.kie_api_base}/api/v1/jobs/createTask"
    payload: dict[str, Any] = {
        "model": settings.kie_model,
        "callBackUrl": settings.kie_callback_url,
        "input": {
            "prompt": full_prompt,
            "negative_prompt": NEGATIVE_PROMPT,
            "cfg_scale": 0.7,
            "input_urls": [image_url],
            "video_urls": [video_url],
            "mode": "720p",
            "character_orientation": "video",
            "background_source": "input_video",
        },
    }
    headers = {
        "Authorization": f"Bearer {settings.kie_api_key}",
        "Content-Type": "application/json",
    }
    async with session.post(url, json=payload, headers=headers, timeout=aiohttp.ClientTimeout(total=60)) as resp:
        body = await _response_json_or_error(resp, "createTask")
    code = body.get("code")
    if code != 200:
        raise KieError(code, body.get("msg", str(body)))
    data = body.get("data") or {}
    task_id = data.get("taskId")
    if not task_id:
        raise KieError(code, f"No taskId: {body}")
    return str(task_id)


async def get_task_info(session: aiohttp.ClientSession, settings: Settings, task_id: str) -> dict[str, Any]:
    url = f"{settings.kie_api_base}/api/v1/jobs/recordInfo"
    params = {"taskId": task_id}
    headers = {"Authorization": f"Bearer {settings.kie_api_key}"}
    async with session.get(url, params=params, headers=headers, timeout=aiohttp.ClientTimeout(total=30)) as resp:
        body = await _response_json_or_error(resp, "recordInfo")
    if body.get("code") != 200:
        raise KieError(body.get("code"), body.get("msg", str(body)))
    return body.get("data") or {}


def parse_result_video_url(data: dict[str, Any]) -> str | None:
    raw = data.get("resultJson")
    if not raw:
        return None
    try:
        obj = json.loads(raw) if isinstance(raw, str) else raw
    except json.JSONDecodeError:
        logger.warning("Bad resultJson: %s", raw)
        return None
    urls = obj.get("resultUrls") or obj.get("result_urls")
    if isinstance(urls, list) and urls:
        return str(urls[0])
    return None
