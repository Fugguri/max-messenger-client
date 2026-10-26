"""maxmessenger — неофициальный клиент WS-протокола MAX (web.max.ru), reverse-engineered.

Проверено (тесты): кодек кадров, msgpack-слой, кодировка id.
Экспериментально (не гонялось против сервера): live-клиент, HELLO/AUTH, отправка.
См. PROTOCOL.md.
"""

from __future__ import annotations

from . import codec, formatting, media, opcodes
from .client import DEFAULT_WS_URL, MaxClient
from .codec import Id
from .device import Device
from .formatting import html_to_message
from .protocol import Frame

__version__ = "0.1.0"
__all__ = [
    "MaxClient",
    "DEFAULT_WS_URL",
    "Frame",
    "Device",
    "Id",
    "html_to_message",
    "codec",
    "formatting",
    "media",
    "opcodes",
    "__version__",
]
