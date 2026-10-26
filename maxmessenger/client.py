"""Async-клиент MAX поверх raw WebSocket.

Статус: кодек кадров и msgpack-слой проверены (см. tests). Живой handshake
(HELLO/AUTH точные payload'ы, URL WS-эндпоинта) и отправка **против реального
сервера не гонялись** — помечено в докстрингах. Это скелет поверх разобранного
протокола, а не «проверено в бою».
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Awaitable, Callable

import websockets

from . import codec, media, opcodes
from .device import Device
from .protocol import DIR_RESPONSE, Frame

logger = logging.getLogger(__name__)

EventHandler = Callable[[Frame], Awaitable[None] | None]


#: WS-эндпоинт MAX по умолчанию (менять не нужно)
DEFAULT_WS_URL = "wss://api.oneme.ru/websocket"


class MaxClient:
    def __init__(
        self,
        token: str,
        *,
        device: Device | None = None,
        url: str = DEFAULT_WS_URL,
        request_timeout: float = 15.0,
        max_retries: int = 3,
        backoff_base: float = 0.5,
        backoff_max: float = 8.0,
    ) -> None:
        self.url = url
        self.token = token
        self.device = device or Device()
        self._ws: websockets.WebSocketClientProtocol | None = None
        self._seq = 0
        self._pending: dict[int, asyncio.Future] = {}
        self._recv_task: asyncio.Task | None = None
        self._timeout = request_timeout
        self._max_retries = max_retries
        self._backoff_base = backoff_base
        self._backoff_max = backoff_max
        self.session: dict | None = (
            None  # дамp аккаунта из AUTH (chats/contacts/profile)
        )
        self.on_event: EventHandler | None = None

    # ── соединение ──────────────────────────────────────────────────────────
    #: заголовки WS-рукопожатия — сервер проверяет Origin (иначе 403)
    ORIGIN = "https://web.max.ru"

    async def connect(self) -> None:
        """Открыть WS и запустить приём. Шлём Origin/User-Agent как браузер —
        иначе сервер отвечает 403 на upgrade."""
        self._ws = await websockets.connect(
            self.url,
            max_size=None,
            origin=self.ORIGIN,
            user_agent_header=self.device.header_user_agent,
        )
        self._recv_task = asyncio.create_task(self._recv_loop())
        logger.info("MAX WS: соединение открыто")

    async def close(self) -> None:
        if self._recv_task:
            self._recv_task.cancel()
        if self._ws:
            await self._ws.close()

    async def _recv_loop(self) -> None:
        assert self._ws is not None
        try:
            async for raw in self._ws:
                if not isinstance(raw, (bytes, bytearray)):
                    continue
                try:
                    frame = Frame.decode(bytes(raw))
                except ValueError:
                    logger.warning("MAX WS: битый кадр", exc_info=True)
                    continue
                fut = self._pending.pop(frame.seq, None)
                if frame.direction == DIR_RESPONSE and fut and not fut.done():
                    frame.data = self._safe_decode(frame)
                    fut.set_result(frame)
                elif self.on_event is not None:
                    frame.data = self._safe_decode(frame)
                    res = self.on_event(frame)
                    if asyncio.iscoroutine(res):
                        await res
                # иначе — входящее без подписчика (чужое сообщение/presence): вычитали
                # из сокета и игнорируем, payload НЕ декодим (экономим CPU)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("MAX WS: recv-loop упал")

    @staticmethod
    def _safe_decode(frame: Frame):
        """Декод payload кадра, None при битом/не-msgpack (msgpack+LZ4)."""
        try:
            return codec.decode_payload(frame.payload, frame.flags)
        except Exception:  # noqa: BLE001 — payload не msgpack/битый
            return None

    # ── запрос/ответ ────────────────────────────────────────────────────────
    def _next_seq(self) -> int:
        self._seq = (self._seq + 1) & 0xFFFF
        return self._seq

    async def _send_once(
        self, opcode: int, obj=None, *, timeout: float | None = None
    ) -> Frame:
        if self._ws is None:
            raise RuntimeError("не подключено")
        seq = self._next_seq()
        payload, flags = codec.encode_payload(obj) if obj is not None else (b"", 0)
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[seq] = fut
        await self._ws.send(
            Frame(opcode=opcode, payload=payload, seq=seq, flags=flags).encode()
        )
        logger.debug("→ %s seq=%d flags=%d", opcodes.name(opcode), seq, flags)
        try:
            return await asyncio.wait_for(fut, timeout or self._timeout)
        finally:
            self._pending.pop(seq, None)  # не копим «висячие» future при таймауте

    async def request(self, opcode: int, obj=None) -> Frame:
        """Запрос с ретраями: при обрыве/таймауте — реконнект (hello+auth) и повтор.

        obj → msgpack (+ LZ4-сжатие при размере > порога) автоматически.
        """
        last: Exception | None = None
        for attempt in range(self._max_retries + 1):
            try:
                return await self._send_once(opcode, obj)
            except (
                websockets.ConnectionClosed,
                asyncio.TimeoutError,
                RuntimeError,
            ) as exc:
                last = exc
                if attempt >= self._max_retries:
                    break
                delay = min(self._backoff_base * (2**attempt), self._backoff_max)
                logger.warning(
                    "MAX WS: %s на %s, реконнект через %.1fs (попытка %d)",
                    exc.__class__.__name__,
                    opcodes.name(opcode),
                    delay,
                    attempt + 1,
                )
                await asyncio.sleep(delay)
                try:
                    await self._reconnect()
                except Exception:  # noqa: BLE001 — реконнект не удался, следующая попытка
                    logger.exception("MAX WS: реконнект не удался")
        raise RuntimeError(
            f"запрос {opcodes.name(opcode)} не прошёл после ретраев"
        ) from last

    async def _reconnect(self) -> None:
        """Переоткрыть WS и заново пройти handshake (hello+auth)."""
        try:
            await self.close()
        except Exception:  # noqa: BLE001
            pass
        await self.connect()
        await self.hello()
        await self.auth()

    # ── операции ──────────────────────────────────────────────────────────────
    async def start(self) -> "MaxClient":
        """Полное подключение одним вызовом: connect → hello → auth. Возвращает self.

        Удобный вход: `c = await MaxClient(token).start()` или
        `async with MaxClient(token) as c: ...` (контекст делает start сам)."""
        await self.connect()
        await self.hello()
        await self.auth()
        return self

    async def hello(self) -> Frame:
        """HELLO с device fingerprint (opcode 6). Сервер инициирует сессию."""
        return await self._send_once(opcodes.HELLO, self.device.hello_payload())

    #: сколько чатов тянуть в AUTH-дампе. chatsCount — ЖЁСТКИЙ лимит (проверено:
    #: 1→1,2→2,…), 0 отдаёт урезанный дефолт на больших аккаунтах → пропадают группы.
    #: 100 — безопасный максимум (200+ ловит таймаут сервера). Для >100 групп нужна пагинация.
    CHATS_COUNT = 100

    async def auth(self) -> Frame:
        """AUTH токеном (opcode 13). Ответ = дамп аккаунта: profile, chats,
        contacts, presence, config… Сохраняем в self.session.

        chatsCount ограничивает число чатов в дампе (пагинация страницами по ≤100).
        get_groups() читает эту страницу — при >100 чатов часть групп не попадёт."""
        frame = await self._send_once(
            opcodes.AUTH,
            {
                "token": self.token,
                "chatsCount": self.CHATS_COUNT,
                "interactive": True,
                "chatsSync": 0,
                "contactsSync": 0,
                "presenceSync": -1,
                "draftsSync": 0,
            },
        )
        if isinstance(frame.data, dict):
            self.session = frame.data
        return frame

    # ── удобные геттеры (из дампа AUTH) ──────────────────────────────────────
    def get_chats(self) -> list:
        """Все чаты (после auth). type: 'CHAT' = группа, 'DIALOG' = личка."""
        return (self.session or {}).get("chats", [])

    def get_groups(self) -> list:
        """Только группы (type == 'CHAT'). ⚠ Читает уже загруженные чаты — для полного
        списка на больших аккаунтах сначала вызови `load_all_chats()`."""
        return [c for c in self.get_chats() if c.get("type") == "CHAT"]

    async def load_all_chats(
        self, *, page_size: int = 50, max_pages: int = 100
    ) -> list:
        """Догрузить ВСЕ чаты пагинацией (opcode 0x35), т.к. AUTH отдаёт лишь первую
        страницу (≤100). Курсор `marker` (timestamp) из ответа → в следующий запрос,
        пока страницы не кончатся. Дедуп по id, обновляет self.session['chats']."""
        chats = list(self.get_chats())
        seen = {int(c["id"]) for c in chats if c.get("id") is not None}
        marker = int(time.time() * 1000)
        for _ in range(max_pages):
            frame = await self.request(opcodes.CHATS_PAGE, {"marker": marker})
            data = frame.data if isinstance(frame.data, dict) else {}
            page = data.get("chats") or []
            for c in page:
                cid = c.get("id")
                if cid is not None and int(cid) not in seen:
                    seen.add(int(cid))
                    chats.append(c)
            nxt = data.get("marker")
            nxt = int(nxt) if nxt is not None else None
            # конец: пустая/неполная страница или курсор перестал уменьшаться
            if not page or len(page) < page_size or nxt is None or nxt >= marker:
                break
            marker = nxt
        if self.session is not None:
            self.session["chats"] = chats
        logger.info("MAX: загружено чатов всего %d", len(chats))
        return chats

    async def get_all_groups(self) -> list:
        """Все группы аккаунта (с пагинацией). Гарантированно полный список."""
        await self.load_all_chats()
        return self.get_groups()

    def get_contacts(self) -> list:
        """Контакты аккаунта (после auth)."""
        return (self.session or {}).get("contacts", [])

    def get_profile(self) -> dict:
        """Свой профиль (после auth, из дампа AUTH — без отдельного запроса).
        Сырой объект: {contact:{id,names,phone,…}, restrictions, profileOptions}."""
        return (self.session or {}).get("profile", {})

    def get_own_contact(self) -> dict:
        """Свой contact-объект (id/names/phone/country) из профиля."""
        return self.get_profile().get("contact") or {}

    def get_own_id(self) -> int | None:
        """Свой user id (viewerId) из профиля."""
        cid = self.get_own_contact().get("id")
        return int(cid) if cid is not None else None

    def get_display_name(self) -> str:
        """Своё отображаемое имя (первое из names) или ''."""
        names = self.get_own_contact().get("names") or []
        return (names[0].get("name") if names else "") or ""

    def get_own_phone(self) -> str | None:
        """Свой номер в E.164 (`+7…`) или None. В дампе номер — uint64, приводим к строке."""
        ph = self.get_own_contact().get("phone")
        return f"+{int(ph)}" if ph is not None else None

    def get_chat(self, chat_id: int) -> dict | None:
        """Найти объект чата в дампе AUTH по id (id групп бывают отрицательные)."""
        for c in self.get_chats():
            cid = c.get("id")
            if cid is not None and int(cid) == int(chat_id):
                return c
        return None

    def get_participant_ids(self, chat_id: int) -> list[int]:
        """Id участников группы из map `participants` объекта чата (ключи = user id).

        ⚠ Для больших групп дамп AUTH может содержать participants не целиком
        (сверяйся с полем participantsCount). Полная постраничная выгрузка —
        отдельный опкод, пока не разобран."""
        chat = self.get_chat(chat_id) or {}
        parts = chat.get("participants") or {}
        return [int(k) for k in parts.keys()]

    async def resolve_contacts(self, ids) -> list[dict]:
        """Резолв id→профиль (opcode CONTACTS 0x20). Возвращает список контактов
        с `names`/`phone`. Батч по любым user id (в т.ч. не из списка контактов)."""
        ids = [int(i) for i in ids]
        if not ids:
            return []
        frame = await self.request(opcodes.CONTACTS, codec.msg_resolve_contacts(ids))
        data = frame.data if isinstance(frame.data, dict) else {}
        return data.get("contacts", [])

    async def get_history(
        self, chat_id: int, count: int = 30, from_time: int | None = None
    ) -> list:
        """История сообщений чата (opcode 0x31). Грузит `count` сообщений назад от
        `from_time` (мс, по умолчанию сейчас). → список сообщений (новые→старые)."""
        ts = from_time if from_time is not None else int(time.time() * 1000)
        frame = await self.request(
            opcodes.GET_HISTORY,
            {
                "chatId": codec.Id(int(chat_id)),
                "from": ts,
                "forward": 0,
                "backward": int(count),
                "getMessages": True,
            },
        )
        data = frame.data if isinstance(frame.data, dict) else {}
        return data.get("messages", [])

    async def mark_read(
        self, chat_id: int, message_id: int, *, mark_time: int | None = None
    ) -> Frame:
        """Отметить чат прочитанным до message_id (opcode 0x32)."""
        ts = mark_time if mark_time is not None else int(time.time() * 1000)
        return await self.request(
            opcodes.MARK_READ,
            {
                "type": "READ_MESSAGE",
                "chatId": codec.Id(int(chat_id)),
                "messageId": codec.Id(int(message_id)),
                "mark": ts,
            },
        )

    async def edit_message(
        self,
        chat_id: int,
        message_id: int,
        text: str,
        *,
        elements: list | None = None,
        attachments: list | None = None,
    ) -> Frame:
        """Редактировать своё сообщение (opcode 0x43). Ключ вложений — `attachments`
        (в отличие от send, где `attaches`); чтобы сохранить фото — передай его attach."""
        return await self.request(
            opcodes.EDIT_MESSAGE,
            {
                "chatId": codec.Id(int(chat_id)),
                "messageId": codec.Id(int(message_id)),
                "text": text,
                "elements": elements or [],
                "attachments": attachments or [],
            },
        )

    async def create_group(self, title: str, user_ids) -> dict | None:
        """Создать группу (SEND_MESSAGE 0x40 с CONTROL-attach). user_ids — начальные
        участники (кроме себя). Возвращает объект нового чата (с id) или None."""
        cid = int(time.time() * 1000)
        obj = {
            "message": {
                "cid": codec.Id(cid),
                "attaches": [
                    {
                        "_type": "CONTROL",
                        "event": "new",
                        "chatType": "CHAT",
                        "title": title,
                        "userIds": [codec.Id(int(u)) for u in user_ids],
                    }
                ],
            },
            "notify": True,
        }
        frame = await self.request(opcodes.SEND_MESSAGE, obj)
        data = frame.data if isinstance(frame.data, dict) else {}
        return data.get("chat") or None

    async def delete_chat(
        self, chat_id: int, *, last_event_time: int | None = None, for_all: bool = True
    ) -> Frame:
        """Удалить/выйти из чата (opcode 0x34). for_all=True — у всех (для своих групп);
        False — только у себя."""
        ts = last_event_time if last_event_time is not None else int(time.time() * 1000)
        return await self.request(
            opcodes.DELETE_CHAT,
            {
                "chatId": codec.Id(int(chat_id)),
                "lastEventTime": int(ts),
                "forAll": for_all,
            },
        )

    async def get_presence(self, ids) -> dict:
        """Онлайн-статус пользователей (opcode 0x23) → {presence:{id:{...}}}.
        Пустой presence = офлайн/нет данных."""
        ids = [int(i) for i in ids]
        if not ids:
            return {}
        frame = await self.request(
            opcodes.GET_MEMBERS, {"contactIds": [codec.Id(i) for i in ids]}
        )
        data = frame.data if isinstance(frame.data, dict) else {}
        return data.get("presence", {})

    async def sync_chats(self, chat_ids) -> list:
        """Метаданные чатов по id (opcode 0x30 SYNC_CHAT) → список объектов чата
        (title, type, participantsCount, lastMessage, …). Обновляет self.session-кэш неявно."""
        ids = [int(i) for i in chat_ids]
        if not ids:
            return []
        frame = await self.request(
            opcodes.SYNC_CHAT, {"chatIds": [codec.Id(i) for i in ids]}
        )
        data = frame.data if isinstance(frame.data, dict) else {}
        return data.get("chats", [])

    async def find_by_phone(self, phone: str, *, timeout: float = 6.0) -> dict | None:
        """Найти пользователя MAX по номеру (opcode 0x2e). phone — E.164 (+7…).
        Возвращает контакт {id, names, …} или None.

        ⚠ На НЕсуществующий номер сервер молча не отвечает → таймаут трактуем как
        «не найден» (без реконнект-ретраев, чтобы батч номеров не тормозил)."""
        try:
            frame = await self._send_once(
                opcodes.SEARCH_PHONE, codec.msg_search_phone(phone), timeout=timeout
            )
        except (asyncio.TimeoutError, websockets.ConnectionClosed):
            return None
        data = frame.data if isinstance(frame.data, dict) else {}
        return data.get("contact") or None

    async def list_group_members(self, chat_id: int) -> list[dict]:
        """Участники группы с именами: `participants` (id) + резолв имён через 0x20.
        Возвращает [{id, name, phone}]. Имя может быть пустым, если сервер не отдал."""
        ids = self.get_participant_ids(chat_id)
        if not ids:
            return []
        contacts = await self.resolve_contacts(ids)
        by_id = {int(c["id"]): c for c in contacts if c.get("id") is not None}
        out: list[dict] = []
        for uid in ids:
            c = by_id.get(uid, {})
            names = c.get("names") or []
            name = (names[0].get("name") if names else "") or ""
            out.append({"id": uid, "name": name, "phone": c.get("phone")})
        return out

    async def send_message(
        self,
        chat_id: int,
        text: str,
        *,
        notify: bool = True,
        elements: list | None = None,
    ) -> Frame:
        """Отправить сообщение (opcode 64). elements — форматирование
        (см. maxmessenger.formatting.html_to_message)."""
        cid = int(time.time() * 1000)  # клиентский id сообщения
        obj = codec.msg_send(chat_id, text, cid, notify=notify, elements=elements)
        return await self.request(opcodes.SEND_MESSAGE, obj)

    def dm_chat_id(self, user_id: int) -> int:
        """Id личного чата (DIALOG) с пользователем = **user_id XOR own_id** (формула MAX).

        Работает БЕЗ открытия диалога и БЕЗ добавления в контакты — первое сообщение
        новому человеку уходит сразу. Для себя даёт 0 (Избранное: own XOR own = 0)."""
        own = self.get_own_id()
        if own is None:
            raise RuntimeError("нет own_id — сначала auth()")
        return int(user_id) ^ int(own)

    async def send_dm(
        self,
        user_id: int,
        text: str,
        *,
        notify: bool = True,
        elements: list | None = None,
    ) -> Frame:
        """Отправить ЛС пользователю по его user_id (сам считает id диалога через XOR).
        Не требует, чтобы диалог уже существовал."""
        return await self.send_message(
            self.dm_chat_id(user_id), text, notify=notify, elements=elements
        )

    async def get_members(self, chat_id: int) -> Frame:
        """Запросить участников чата (opcode 35)."""
        return await self.request(opcodes.GET_MEMBERS, codec.msg_get_members(chat_id))

    async def delete_message(
        self, chat_id: int, message_id: int, *, for_me: bool = False
    ) -> Frame:
        """Удалить сообщение (opcode 66). for_me=False → у всех (для отката рассылки);
        for_me=True → только у себя. Удаление «у всех» обычно ограничено по времени."""
        return await self.request(
            opcodes.DELETE_MESSAGE,
            {
                "chatId": codec.Id(int(chat_id)),
                "messageIds": [codec.Id(int(message_id))],
                "forMe": for_me,
            },
        )

    # ── медиа (фото) ─────────────────────────────────────────────────────────
    async def request_upload_url(self, count: int = 1) -> str:
        """Запросить CDN-url для загрузки фото (opcode 0x50). Возвращает url
        (iu.oneme.ru/uploadImage?apiToken=…&photoIds=…). count — число фото."""
        frame = await self.request(
            opcodes.REQUEST_UPLOAD, codec.msg_request_upload(count)
        )
        data = frame.data if isinstance(frame.data, dict) else {}
        url = data.get("url") or (data.get("urls") or [None])[0]
        if not url:
            raise RuntimeError(f"REQUEST_UPLOAD без url: {data!r}")
        return url

    async def upload_photo(self, path: str) -> str:
        """Загрузить фото на CDN и вернуть photoToken для attaches.

        Шаги: request_upload_url (WS) → POST файла (HTTP, в отдельном потоке) →
        достать token из ответа `{photos:{<id>:{token}}}`.
        """
        with open(path, "rb") as fh:
            content = fh.read()
        filename = os.path.basename(path) or "image"
        ctype = media.guess_content_type(path)
        url = await self.request_upload_url(1)
        resp = await asyncio.to_thread(
            media.upload_to_cdn, url, content, filename, ctype
        )
        token = media.extract_photo_token(resp)
        logger.info("MAX: фото залито (%s), photoToken получен", filename)
        return token

    async def send_photo(
        self,
        chat_id: int,
        path: str,
        *,
        caption: str = "",
        notify: bool = True,
        elements: list | None = None,
    ) -> Frame:
        """Отправить фото (при желании с подписью). Загружает на CDN и шлёт SEND
        с attaches:[PHOTO]. caption — текст-подпись, elements — форматирование."""
        token = await self.upload_photo(path)
        cid = int(time.time() * 1000)
        obj = codec.msg_send(
            chat_id,
            caption,
            cid,
            notify=notify,
            elements=elements,
            attaches=[codec.photo_attach(token)],
        )
        return await self.request(opcodes.SEND_MESSAGE, obj)

    async def __aenter__(self) -> "MaxClient":
        """Контекст-вход = полный старт (connect+hello+auth), клиент сразу готов."""
        await self.start()
        return self

    async def __aexit__(self, *exc) -> None:
        await self.close()
