"""Загрузка медиа в MAX: HTTP-часть (CDN) отдельно от WS.

Флоу фото (разобран перехватом web.max.ru):
  1. WS-запрос REQUEST_UPLOAD (0x50) `{count: N}` → ответ `{url: "https://iu.oneme.ru/uploadImage?apiToken=…&photoIds=…"}`.
  2. HTTP POST файла на этот url как multipart/form-data (одно поле-файл) →
     ответ JSON `{"photos": {"<photoId>": {"token": "<photoToken>"}}}`.
  3. WS-SEND (0x40) с `attaches: [{_type:"PHOTO", photoToken:"<token>"}]`.

Здесь — шаг 2 (HTTP). Шаги 1 и 3 — в client.py. HTTP-вызов синхронный (urllib,
без лишних зависимостей); клиент гоняет его через asyncio.to_thread.
"""
from __future__ import annotations

import json
import logging
import mimetypes
import os
import urllib.request

logger = logging.getLogger(__name__)

# имя multipart-поля с файлом (web-клиент шлёт файл единственным полем)
_FILE_FIELD = "file"


def _multipart(field: str, filename: str, content: bytes, content_type: str) -> tuple[bytes, str]:
    """Собрать тело multipart/form-data с одним файловым полем. → (тело, boundary)."""
    boundary = "----maxws" + os.urandom(12).hex()
    crlf = b"\r\n"
    head = (
        f'--{boundary}\r\n'
        f'Content-Disposition: form-data; name="{field}"; filename="{filename}"\r\n'
        f"Content-Type: {content_type}\r\n\r\n"
    ).encode()
    body = head + content + crlf + f"--{boundary}--\r\n".encode()
    return body, boundary


def guess_content_type(path: str) -> str:
    return mimetypes.guess_type(path)[0] or "application/octet-stream"


def upload_to_cdn(url: str, content: bytes, filename: str, content_type: str,
                  *, timeout: float = 60.0) -> dict:
    """POST файла на выданный CDN-url (шаг 2). Возвращает распарсенный JSON-ответ.

    Синхронный (urllib). Бросает при не-2xx или неразборном ответе — глотать нельзя,
    иначе шаг 3 уйдёт с пустым токеном.
    """
    body, boundary = _multipart(_FILE_FIELD, filename, content, content_type)
    req = urllib.request.Request(
        url, data=body, method="POST",
        headers={
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Origin": "https://web.max.ru",
            "Referer": "https://web.max.ru/",
        },
    )
    logger.info("MAX CDN: заливаю %s (%d байт, %s)", filename, len(content), content_type)
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 — доверенный CDN MAX
        raw = resp.read()
    data = json.loads(raw.decode("utf-8"))
    logger.info("MAX CDN: ответ получен (%d байт)", len(raw))
    return data


def extract_photo_token(upload_response: dict) -> str:
    """Достать photoToken из ответа CDN `{photos:{<id>:{token}}}` (шаг 2 → шаг 3)."""
    photos = upload_response.get("photos") or {}
    if not photos:
        raise ValueError(f"в ответе CDN нет photos: {upload_response!r}")
    first = next(iter(photos.values()))
    token = first.get("token")
    if not token:
        raise ValueError(f"в ответе CDN нет token: {upload_response!r}")
    return token
