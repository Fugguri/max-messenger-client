"""Опкоды WS-протокола MAX (опознаны из живого трейса).

Достоверность: send/get_members/auth/hello подтверждены перехватом; остальные —
по контексту фаз (открытие чата, синки). Уточняются по мере разбора.
"""
from __future__ import annotations

HELLO = 0x06          # параметры устройства (device fingerprint)
AUTH = 0x13           # токен + первичный синк (ответ содержит все чаты)
SYNC_CHAT = 0x30      # {chatIds:[id]}
CHATS_PAGE = 0x35     # {marker:ts} → {chats:[…50], marker:next} — пагинация списка чатов
CONTACTS = 0x20       # {contactIds:[id,…]} → профили с именами (резолвер id→имя)
FOLDER_SYNC = 0x10
GET_HISTORY = 0x31    # {chatId, from, forward, backward, getMessages} → {messages:[…]}
MARK_READ = 0x32      # {type:"READ_MESSAGE", chatId, messageId, mark} — отметить прочитанным
DELETE_CHAT = 0x34    # {chatId, lastEventTime, forAll} — удалить/выйти из чата
EDIT_MESSAGE = 0x43   # {chatId, messageId, text, elements, attachments} — редактировать
# create group = SEND_MESSAGE(0x40) с attach {_type:CONTROL, event:new, chatType:CHAT, title, userIds}
GET_MEMBERS = 0x23    # {contactIds:[id]} → {presence:{…}} — ЭТО presence, НЕ список участников
SUBSCRIBE_CHAT = 0x4B  # {chatId, subscribe:bool} — подписка на live-апдейты чата
COMPOSING = 0x41      # {chatId, type:"TEXT"} — pre-send / «печатает»
SEND_MESSAGE = 0x40   # см. codec.build_send
DELETE_MESSAGE = 0x42  # {chatId, messageIds:[id], forMe} — forMe:false = у всех
REQUEST_UPLOAD = 0x50  # {count:N} → {url: iu.oneme.ru/uploadImage?apiToken=…&photoIds=…}
SEARCH_PHONE = 0x2E    # {phone:"+7…"} → {contact:{id,names,…}} (поиск юзера по номеру)
SETTINGS_SYNC = 0x1B
PRESENCE = 0x1A

# читаемые имена для логов/отладки
NAMES = {
    HELLO: "HELLO", AUTH: "AUTH", SYNC_CHAT: "SYNC_CHAT", CONTACTS: "CONTACTS",
    CHATS_PAGE: "CHATS_PAGE",
    FOLDER_SYNC: "FOLDER_SYNC", GET_HISTORY: "GET_HISTORY", MARK_READ: "MARK_READ",
    EDIT_MESSAGE: "EDIT_MESSAGE", DELETE_CHAT: "DELETE_CHAT", GET_MEMBERS: "GET_MEMBERS",
    COMPOSING: "COMPOSING", SEND_MESSAGE: "SEND_MESSAGE", DELETE_MESSAGE: "DELETE_MESSAGE",
    REQUEST_UPLOAD: "REQUEST_UPLOAD", SEARCH_PHONE: "SEARCH_PHONE", SUBSCRIBE_CHAT: "SUBSCRIBE_CHAT",
    SETTINGS_SYNC: "SETTINGS_SYNC", PRESENCE: "PRESENCE",
}


def name(opcode: int) -> str:
    return NAMES.get(opcode, f"0x{opcode:02x}")
