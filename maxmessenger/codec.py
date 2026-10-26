"""msgpack-слой протокола MAX.

- ID чатов/пользователей = msgpack **ext type 1**, data = 0xCF + uint64(8, BE)
  (т.е. внутри ext лежит msgpack-uint64). Представляем классом `Id(int)`.
- payload кадра — msgpack. При `flags=0` это один map; при `flags=2` перед map
  идут ведущие скаляры (семантика уточняется) — поэтому читаем ПОТОКОМ.
"""

from __future__ import annotations

import math

import lz4.block
import msgpack

EXT_ID = 1
COMPRESS_THRESHOLD = 32  # payload > 32 байт → сжимается LZ4 (порог Qne из бандла)


class Id:
    """MAX id (chat/user). Сериализуется как msgpack ext type 1 (uint64).

    Не наследуем int — иначе msgpack паковал бы его как обычный int, минуя ext.
    """

    __slots__ = ("value",)

    def __init__(self, value) -> None:
        self.value = int(value)

    def __int__(self) -> int:
        return self.value

    def __index__(self) -> int:
        return self.value

    def __eq__(self, other) -> bool:
        if isinstance(other, Id):
            return self.value == other.value
        if isinstance(other, int):
            return self.value == other
        return NotImplemented

    def __hash__(self) -> int:
        return hash(self.value)

    def __repr__(self) -> str:
        return f"Id({self.value})"


def _ext_hook(code: int, data: bytes):
    # ext type 1 = id: данные — это msgpack-целое (uint64 cf / int64 d3 / fixint)
    if code == EXT_ID:
        return Id(msgpack.unpackb(data, raw=False, strict_map_key=False))
    return msgpack.ExtType(code, data)


def _default(obj):
    if isinstance(obj, Id):
        v = int(obj)
        # id групп — ОТРИЦАТЕЛЬНЫЕ: кодируем как msgpack int64 (0xd3), иначе uint64 (0xcf).
        # Ширину держим 8 байт, как реальный клиент. unsigned для отрицательного = OverflowError.
        if v < 0:
            return msgpack.ExtType(EXT_ID, b"\xd3" + v.to_bytes(8, "big", signed=True))
        return msgpack.ExtType(EXT_ID, b"\xcf" + v.to_bytes(8, "big"))
    raise TypeError(f"не сериализуется: {type(obj)}")


def pack(obj) -> bytes:
    """msgpack-сериализация одного объекта (с поддержкой Id)."""
    return msgpack.packb(obj, use_bin_type=True, default=_default)


def unpack_all(payload: bytes) -> list:
    """Прочитать ВСЕ msgpack-значения из payload (для flags=2 их несколько)."""
    unp = msgpack.Unpacker(raw=False, strict_map_key=False, ext_hook=_ext_hook)
    unp.feed(payload)
    return list(unp)


def unpack_map(payload: bytes) -> dict:
    """Достать целевой map из payload (последнее значение потока)."""
    vals = unpack_all(payload)
    for v in reversed(vals):
        if isinstance(v, dict):
            return v
    raise ValueError("в payload нет map")


def encode_payload(obj) -> tuple[bytes, int]:
    """Объект → (байты, flags) для кадра. Если msgpack > порога — сжать LZ4-block.

    flags = ceil(raw/compressed) (0 = не сжато), как в веб-клиенте MAX.
    """
    raw = pack(obj)
    if len(raw) > COMPRESS_THRESHOLD:
        comp = lz4.block.compress(raw, store_size=False)
        flags = min(math.ceil(len(raw) / len(comp)), 255)
        if flags > 0:
            return comp, flags
    return raw, 0


def decode_payload(data: bytes, flags: int):
    """Байты payload + flags → объект. flags>0 → сначала LZ4-распаковка."""
    if flags > 0:
        data = lz4.block.decompress(data, uncompressed_size=len(data) * flags)
    return msgpack.unpackb(data, raw=False, strict_map_key=False, ext_hook=_ext_hook)


# ── билдеры объектов payload (клиент сам сожмёт через encode_payload) ─────────
def msg_send(
    chat_id: int,
    text: str,
    cid: int,
    *,
    notify: bool = True,
    elements: list | None = None,
    attaches: list | None = None,
) -> dict:
    """Объект SEND_MESSAGE (0x40): {chatId, message:{text,cid,elements,attaches}, notify}."""
    return {
        "chatId": Id(chat_id),
        "message": {
            "text": text,
            "cid": Id(cid),  # client message id — тоже ext type 1
            "elements": elements or [],
            "attaches": attaches or [],
        },
        "notify": notify,
    }


def msg_get_members(chat_id: int) -> dict:
    """Объект 0x23: {contactIds:[id]}. ⚠ Это presence-запрос, НЕ список участников."""
    return {"contactIds": [Id(chat_id)]}


def msg_resolve_contacts(ids) -> dict:
    """Объект CONTACTS (0x20): {contactIds:[id,…]} → профили с именами/телефонами.
    Резолвер id→имя (в т.ч. для участников группы из map participants)."""
    return {"contactIds": [Id(int(i)) for i in ids]}


def msg_search_phone(phone: str) -> dict:
    """Объект SEARCH_PHONE (0x2e): {phone:"+7…"}. Номер — E.164 с кодом страны."""
    return {"phone": phone}


def msg_composing(chat_id: int, kind: str = "TEXT") -> dict:
    """Объект COMPOSING (0x41): {chatId, type}. kind: TEXT / PHOTO / VIDEO / FILE / AUDIO."""
    return {"chatId": Id(chat_id), "type": kind}


def photo_attach(token: str) -> dict:
    """Attach фото для SEND: {_type:'PHOTO', photoToken}. token — из ответа CDN."""
    return {"_type": "PHOTO", "photoToken": token}


def msg_request_upload(count: int = 1) -> dict:
    """Объект REQUEST_UPLOAD (0x50): {count:N} → ответ {url:…} (CDN uploadImage)."""
    return {"count": count}


# байтовые обёртки (для тестов/примера — без сжатия)
def build_send(chat_id: int, text: str, cid: int, **kw) -> bytes:
    return pack(msg_send(chat_id, text, cid, **kw))


def build_get_members(chat_id: int) -> bytes:
    return pack(msg_get_members(chat_id))


def build_composing(chat_id: int) -> bytes:
    return pack(msg_composing(chat_id))
