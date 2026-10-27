# maxmessenger — неофициальный клиент MAX (max.ru / web.max.ru) на Python

[![Python](https://img.shields.io/badge/python-3.10%2B-blue)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![tests](https://github.com/Fugguri/max-messenger-client/actions/workflows/ci.yml/badge.svg)](https://github.com/Fugguri/max-messenger-client/actions/workflows/ci.yml)

**maxmessenger** — неофициальный Python-клиент и спецификация WebSocket-протокола
мессенджера **MAX** (`web.max.ru`, `max.ru`): фрейминг, msgpack, id (ext type 1),
LZ4-сжатие (`flags`), HELLO/AUTH, чаты, участники, отправка сообщений и медиа.
Reverse-engineered по перехвату WS-кадров; не связан с VK/MAX.

> **English:** Unofficial Python client and protocol notes for the MAX messenger
> (`max.ru` / `web.max.ru`) WebSocket protocol: frame codec, msgpack, LZ4, HELLO/AUTH,
> chats, media, sending. Reverse-engineered; not affiliated with VK/MAX.

Репозиторий: [Fugguri/max-messenger-client](https://github.com/Fugguri/max-messenger-client)
· Python package: `maxmessenger`.

Если вы ищете неофициальный клиент MAX, Python-клиент MAX или разбор его
WebSocket-протокола — это оно. Полная спека — [`PROTOCOL.md`](PROTOCOL.md).

> **Статус честно.** Полностью разобран и покрыт тестами (публичный
> синтетический трейс; реальные кадры не публикуются):
> **фрейминг, msgpack, кодировка id, LZ4-сжатие payload (`flags`), форматирование
> (`elements`)** — 33 теста. Клиент (`connect`/`hello`/`auth`/`send_message`/
> `get_members` + ретраи) реализован по реверснутому протоколу. **Не хватает
> финального прогона против реального сервера** `wss://api.oneme.ru/websocket`
> (сам код кадров/сжатия совпадает с браузером байт-в-байт).

## Что сделано

Сделан reverse-engineering WebSocket-протокола MAX и публичная Python-реализация:

- **Frame codec** — заголовок `0x0A`, направление, `seq`, опкод, `flags`, u24-длина, payload.
- **msgpack + MAX id** — ext type 1 (uint64/int64), отрицательные id групп.
- **LZ4 payload** — сжатие с `flags = ceil(raw/compressed)`, как в веб-клиенте.
- **Handshake** — `HELLO` (device fingerprint) + `AUTH` (bearer), дамп аккаунта в `client.session`.
- **Чаты и сообщения** — список, история, участники, поиск по номеру, отправка/правка/удаление.
- **Форматирование и медиа** — HTML → `elements`, загрузка фото на CDN и attach.
- **Async-клиент** — `websockets`, авто-реконнект и ретраи.
- **Креды** — `tools/max_creds.py`: один ручной вход в браузере, дальше автоматическое
  получение `token`/`deviceId` из persistent-профиля Playwright.
- **Спецификация и тесты** — `PROTOCOL.md` + 33 теста на фрейминг, msgpack, id, LZ4,
  форматирование и медиа.

## Как делали

1. Залогинились в `web.max.ru`, открыли DevTools → Network → WS и записали бинарные кадры.
2. Разобрали заголовок: `0A · dir · seq(u16) · opcode(u16) · flags · length(u24) · payload`.
3. Поняли, что payload — msgpack; MAX id кодируются как **ext type 1** (`0xCF + uint64`,
   отрицательные — `0xD3 + int64`).
4. Обнаружили LZ4: при `flags > 0` payload сжат block-режимом, `flags = ceil(raw/compressed)`.
5. Собрали обратный кодек и проверяли `decode(raw).encode() == raw` на кадрах из трейса;
   так же восстановили `HELLO`/`AUTH` и device fingerprint.
6. Для публичного репозитория живой трейс заменили **синтетическим**: в реальных кадрах
   были deviceId, чаты и имена. Приватный live-трейс остался вне гита.

## Как работает

```text
web.max.ru (browser)
  localStorage: __oneme_auth -> token
                __oneme_device_id -> deviceId
        │
        ▼
MaxClient(token, device)
  connect   wss://api.oneme.ru/websocket   (Origin: https://web.max.ru)
  hello     0x06  {userAgent, deviceId}
  auth      0x13  {token, chatsCount, ...} -> session/chats/contacts/profile
  request   0x40 / 0x31 / 0x23 / ...       seq + msgpack (+ LZ4)
        │
        ▼
  _recv_loop -> match seq -> Frame.data -> on_event
```

- **Слои**: `protocol.py` — кадры; `codec.py` — msgpack/id/LZ4; `client.py` — connect/hello/
  auth/requests; `device.py` — fingerprint; `media.py`/`formatting.py` — вложения и `elements`.
- **HELLO/AUTH**: `HELLO` отправляет fingerprint с тем же `deviceId`, что в браузере;
  `AUTH` — bearer-токен и лимит чатов; ответ сохраняется в `client.session`.
- **Запросы**: `seq` сопоставляет ответ с ожидающим future; входящие без ожидания уходят
  в `on_event`. Отправка — `0x40`, история — `0x31`, участники — `0x23`.
- **Ретраи**: при обрыве/таймауте клиент переподключается и повторяет `hello` + `auth`.
- **Креды**: `tools/max_creds.py login` один раз открывает браузер; `get` дальше читает
  localStorage headless и обновляет `creds.json`/`device.json`.
- **Ограничение**: полный live-прогон клиента ещё не завершён; капчу/SMS автоматизировать
  нельзя, токен долгоживущий и при протухании нужен повторный ручной вход.

## Установка

```bash
pip install git+https://github.com/Fugguri/max-messenger-client.git
# или локально:
pip install -e ".[dev]"
```

## Что внутри (один модуль)

```
maxmessenger/
  protocol.py   # кодек кадров: 0A·dir·seq·opcode·flags·length·msgpack  (ПРОВЕРЕНО)
  codec.py      # msgpack + Id (ext type 1 = uint64) + сборка payload'ов (ПРОВЕРЕНО)
  opcodes.py    # опкоды (HELLO/AUTH/SEND/GET_MEMBERS/…)
  device.py     # device fingerprint для HELLO (имитация реального устройства)
  client.py     # MaxClient: connect/hello/auth/requests, ретраи; нужен финальный live-прогон
tools/max_creds.py  # вход в MAX + автополучение token/deviceId
PROTOCOL.md          # спецификация протокола
tests/               # pytest: round-trip кодека на публичном синтетическом ws_trace.json
examples/
```

## Формат кадра

```
0A · dir(1) · seq(u16 BE) · opcode(u16 BE) · flags(1) · length(u24 BE) · msgpack
```

## Пример (кодек — работает уже сейчас)

```python
from maxmessenger import Frame, codec
from maxmessenger.codec import Id

# собрать кадр отправки сообщения
payload = codec.build_send(chat_id=123456789, text="привет", cid=1234567890)
raw = Frame(opcode=0x40, payload=payload, seq=1, flags=0x02).encode()

# разобрать любой пришедший кадр
frame = Frame.decode(raw)
print(hex(frame.opcode), codec.unpack_map(frame.payload))
```

Live-клиент (когда допроверим handshake):

```python
import asyncio
from maxmessenger import MaxClient, Device

async def main():
    async with MaxClient(url="wss://...", token="<bearer>", device=Device.load_or_create("dev.json")) as c:
        await c.hello(); await c.auth()
        await c.send_message(chat_id=123456789, text="привет")

asyncio.run(main())
```

## Тесты

```bash
pytest -v
```
Ключевой тест — `test_decode_roundtrip_on_trace_frames`: берёт кадры из
`tests/ws_trace.json` (публичный синтетический fixture) и проверяет, что
`decode(байты).encode() == байты`. Реальный live-трейс в репозиторий не включён.

## Открытые пункты (см. PROTOCOL.md)

1. Семантика `flags=2` и ведущих скаляров.
2. Точные ключи HELLO/AUTH.
3. Парсинг ответов-списков (chats/members/messages).
4. Формат `elements`/`attaches`, ping/keepalive, URL WS-эндпоинта.

## Правовое

RE неофициального клиента российского мессенджера — dual-use. Проект **не связан**
с VK/MAX; товарные знаки и сервис принадлежат правообладателям. Не используйте
для спама, массовых рассылок и нарушения ToS — ответственность на пользователе.
Код — под MIT (см. `LICENSE`).

---

## Получение токена и deviceId (один раз, через браузер)

Полный логин (телефон → **капча** → SMS) автоматизировать нельзя — капча. Но это
разовый шаг: залогинься в **web.max.ru** в обычном браузере, затем в DevTools →
Console выполни:

```js
JSON.parse(localStorage.__oneme_auth).token      // → bearer-токен
localStorage.__oneme_device_id                    // → deviceId (UUID)
JSON.parse(localStorage.__oneme_auth).viewerId    // → твой user id
```

Эти значения передаёшь в клиент. Токен серверный, долгоживущий (refresh-токена нет
— когда протухнет, повторить вход человеком). WS-эндпоинт: `wss://api.oneme.ru/websocket`.

**deviceId делай тем же, что в браузере** — сервер связывает сессию с устройством.
`Device.load_or_create("device.json")` хранит его постоянным между запусками.

### Автоматизация получения кредов

Один раз логинимся вручную в отдельном persistent-профиле Playwright (капчу
и SMS автоматом не пройти), дальше токен забирается без браузера:

```bash
pip install -e '.[login]'
python -m playwright install chromium

python tools/max_creds.py login   # откроется браузер: войди в MAX
python tools/max_creds.py get     # headless: обновляет creds.json + device.json
```

Пишет `~/.config/maxmessenger/creds.json` (0600) и `~/.config/maxmessenger/device.json`
с тем же `deviceId`, что в браузере. Дальше:

```python
import json
from pathlib import Path
from maxmessenger import MaxClient, Device

creds = json.loads(Path("~/.config/maxmessenger/creds.json").expanduser().read_text())
device = Device.load_or_create(Path("~/.config/maxmessenger/device.json").expanduser())
async with MaxClient(creds["token"], device=device) as c:
    ...
```

## Использование (полный цикл)

```python
import asyncio
from maxmessenger import MaxClient, Device, formatting

async def main():
    dev = Device(device_id="00000000-0000-0000-0000-000000000000")          # тот же, что в браузере
    # url по умолчанию (wss://api.oneme.ru/websocket); контекст сам делает
    # connect+hello+auth — клиент сразу готов, дамп аккаунта уже в c.session
    async with MaxClient("<TOKEN>", device=dev) as c:
        print(c.get_display_name(), c.get_own_id())   # свой профиль (из AUTH, без запроса)
        # обычный текст:
        await c.send_message(chat_id=123456789, text="привет")
        # с форматированием (жирный/курсив из HTML редактора):
        text, els = formatting.html_to_message("<b>жирный</b> и обычный")
        await c.send_message(chat_id=123456789, text=text, elements=els)
        # фото (upload на CDN + attach одним вызовом), можно с подписью:
        await c.send_photo(chat_id=123456789, path="photo.jpg", caption="смотри")
        # поиск по номеру, история, правка, удаление:
        contact = await c.find_by_phone("+79161234567")
        msgs = await c.get_history(chat_id=123456789, count=30)
        # await c.edit_message(123456789, message_id, "новый текст")
        # await c.delete_message(123456789, message_id, for_me=False)

asyncio.run(main())
```

Без контекста: `c = await MaxClient("<TOKEN>").start()` (start = connect+hello+auth),
в конце `await c.close()`.

Ретраи: `MaxClient(..., max_retries=3)` — при обрыве/таймауте авто-реконнект
(hello+auth) и повтор запроса. Входящие (чужие сообщения) игнорируются, если не
задан `c.on_event` — сокет вычитывается, payload не декодится.

Фото: `send_photo()` делает три шага (запрос CDN-url → POST файла → SEND с
attach). Подавайте нормальные PNG/JPEG — CDN отклоняет вырожденные картинки.
Детали протокола — в `PROTOCOL.md` → «Медиа». Видео/файлы/аудио пока не разобраны.

## Лицензия

MIT — см. [`LICENSE`](LICENSE).
