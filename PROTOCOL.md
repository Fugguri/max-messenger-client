# MAX (web.max.ru) WebSocket protocol — reverse-engineering notes

Снято с живой залогиненной сессии (Playwright, перехват `framesent`/`framereceived`).
Полный трейс: `ws_trace.json` (137 кадров: handshake → открытие группы → участники → отправка).

## Транспорт

- WebSocket, **бинарные** кадры.
- Полезная нагрузка — **msgpack** (иногда несколько значений подряд, см. `flags`).

## Формат кадра (подтверждён на HELLO/auth/send)

```
offset  size  field
0       1     0x0A          магия/версия (константа)
1       1     dir           0x00 = запрос (client→server), 0x01 = ответ
2       2     seq (u16 BE)  счётчик запросов на соединение; ответ echo-ит seq запроса
4       2     opcode (u16 BE)
6       1     flags         0x00 / 0x01 / 0x02 (влияет на раскладку payload, см. ниже)
7       3     length (u24 BE)  длина payload в байтах
10      N     payload       msgpack (N = length)
```

Проверка: HELLO len=326 = 10 + 316; auth len=803 = 10 + 793; send len=105 = 10 + 95. ✅

### flags и раскладка payload

- `flags=0x00` → payload = **один** msgpack-объект (map). Декодируется как есть.
- `flags=0x02` (и `0x01`) → перед основным map идут **1-2 ведущих msgpack-скаляра**
  (назначение уточняется — вероятно sub-count / внутренний id). Для парсинга читать
  payload **потоком** (`msgpack.Unpacker`), последний объект = целевой map.

### Кодировка ID (чаты/пользователи)

ID приходят как **msgpack ext type 1**, data = 8 байт big-endian uint64 (с ведущим
маркером `cf`, т.е. `cf` + 8 байт). Примеры: Избранное = `0`, участник = `0x17eb2be9`.

## Опкоды (опознанные)

| opcode | операция | направление первого кадра |
|-------:|----------|---------------------------|
| `0x06` | HELLO — параметры устройства | client→server |
| `0x13` | AUTH — токен + первичный синк (ответ содержит ВСЕ чаты) | client→server |
| `0x30` | sync чата: `{chatIds:[id]}` | client→server |
| `0x20` | контакты | client→server |
| `0x10` | folder sync | client→server |
| `0x31` | открыть чат / получить сообщения | client→server |
| `0x23` | **получить участников**: `{contactIds:[id]}` | client→server |
| `0x41` | pre-send / composing: `{chatId:id, type:"TEXT"}` | client→server |
| `0x40` | **отправить сообщение** (см. схему) | client→server |
| `0x1b` | sync (settings/stickers) — несколько подряд | client→server |
| `0x1a` | presence/typing (частые мелкие) | оба |
| `0x1c` | крупные ответы (вероятно сообщения) | server→client |
| `0xb1`,`0xb4` | пост-отправка (read receipts?) | client→server |

## Ключевые схемы (decoded)

### 0x06 HELLO (устройство) — для «имитации реального устройства»
Поля (из ascii-дампа): `userAgent`, `deviceType:"WEB"`, `locale:"en"`, `osVersion:"Linux"`,
`deviceName:"Chrome"`, `appVersion:"2.26.8.10"`, `screen:"1000x1280 1.0x"`,
`timezone:"Europe/Moscow"`, `deviceId:"<UUID>"`, полный `User-Agent` строкой.
→ для либы: генератор устойчивого fingerprint (постоянный `deviceId`, реалистичные поля).

### 0x13 AUTH
`{ token:"<bearer>", ... chatsCount, interactive, ...Sync-флаги }`.
Ответ (len ~6КБ) = первичный дамп чатов.

### 0x23 GET MEMBERS (запрос)
`{ "contactIds": [ <ext1 uint64 id> ] }`

### 0x41 pre-send
`{ "chatId": <ext1 id>, "type": "TEXT" }`

### 0x40 SEND MESSAGE (запрос) — главное
```
{ chatId: <ext1 id>,
  message: { text: "<текст>", cid: <int64 client-id>, elements: [], attaches: [] },
  notify: <bool> }
```
Ответ (0x40, dir=1, тот же seq) — эхо с присвоенным серверным id сообщения.

## Что осталось уточнить для полноты либы

1. Точная семантика `flags` и ведущих скаляров у `flags=2` (сколько их, что значат).
2. Опкоды получения списков в удобном виде (chats/messages) — распарсить ответы 0x13/0x31/0x1c.
3. Формат `elements`/`attaches` (форматирование, вложения).
4. Ping/keepalive кадры.
5. Сверить с JS-бандлом web.max.ru (эталон опкодов/схем).

## Замечание

Неофициальный клиент RE-протокола российского мессенджера — dual-use. Публикация самого
протокола/клиента обычно легальна, но оформление и риски — на усмотрение автора.

---

## Авторизация и креды (получение без повторного логина)

Полный логин (телефон → капча → SMS) **не реверсим** — капча непроходима автоматом.
Вместо этого браузер логинится один раз, а креды забираются из его сессии:

| Что | Где взять |
|-----|-----------|
| **WS-эндпоинт** | `wss://api.oneme.ru/websocket` |
| **token** (bearer) | `localStorage["__oneme_auth"]` → JSON `{"token": "..."}` |
| **deviceId** | `localStorage["__oneme_device_id"]` (UUID) |

Далее клиент открывает WS сам:
1. `0x06 HELLO` — device fingerprint (deviceId тот же, что в браузере).
2. `0x13 AUTH` — `{token, ...sync-флаги}`; ответ = первичный дамп чатов.
3. дальше `0x30`/`0x23`/`0x40` — чаты, участники, отправка.

⚠ Точная msgpack-раскладка HELLO/AUTH при `flags=2` (ведущие скаляры) ещё
подтверждается против живого сервера — до этого live-`connect()` считать
экспериментальным.

---

## Форматирование (elements) — подтверждено перехватом

Композер MAX — редактор **Lexical**, поддерживает Ctrl+B/I/… Формат уходит в
кадре SEND (`0x40`) в `message.elements` — массив entity как в Telegram:

```
message.elements = [ { "type": "STRONG", "from": <char>, "length": <char> }, … ]
```

- Смещения/длины — в **символах** (code points), не в байтах.
- Подтверждено живьём: `text="ЖИРНЫЙТЕКСТ"` → `elements=[{type:"STRONG", from:0, length:11}]`.

| HTML | type MAX | статус |
|------|----------|--------|
| `<b>`/`<strong>` | `STRONG` | ✅ подтверждён |
| `<i>`/`<em>` | `EMPHASIZED` | ⚠ TBD (уточнить Ctrl+I) |
| `<u>` | `UNDERLINE` | ⚠ TBD |
| `<s>` | `STRIKETHROUGH` | ⚠ TBD |
| `<code>` | `MONOSPACE` | ⚠ TBD |

Конвертер `maxws.formatting.html_to_message(html) -> (text, elements)` собирает это
из Telegram-HTML редактора. Тип STRONG проверен тестом против живого кадра.

---

## Медиа — фото (подтверждено перехватом + живой отправкой 2026-08-31)

Отправка фото — три шага (WS → HTTP → WS):

**1. Запрос CDN-url** — WS `REQUEST_UPLOAD` (`0x50`), payload `{count: N}`
(N — число фото). Ответ:

```
{ "url": "https://iu.oneme.ru/uploadImage?apiToken=<...>&photoIds=<...>" }
```

**2. Загрузка байтов** — HTTP `POST` на выданный `url`, тело
`multipart/form-data` с одним полем **`file`** (имя подтверждено хуком
`FormData.append`), `filename` + `Content-Type` по расширению. Ответ:

```
{ "photos": { "<photoId>": { "token": "<photoToken>" } } }
```

`photoId` совпадает со значением `photoIds` из url. `token` → это photoToken.

**3. Отправка** — WS `SEND_MESSAGE` (`0x40`) с attach:

```
message.attaches = [ { "_type": "PHOTO", "photoToken": "<token из шага 2>" } ]
```

Текст-подпись (`message.text`) и `elements` можно слать вместе с фото.
Ответ сервера на SEND содержит сохранённое сообщение: `message.id`,
`attaches[0].baseUrl` (`https://i.oneme.ru/i?r=…&expires=…`), `photoId`,
`width`/`height` — это подтверждение, что фото легло в чат.

⚠ CDN отклоняет «битые»/вырожденные картинки (`{error_code:503,
error_data:IMAGE_INVALID_FORMAT}` — напр. на PNG 16×16). Веб-клиент перед
заливкой перекодирует изображение через canvas; библиотека шлёт файл как есть,
поэтому подавайте нормальные PNG/JPEG. Живой прогон: `real.png` (2.9 КБ) и
`real.jpg` (9.7 КБ) залиты и отправлены успешно.

`COMPOSING` (`0x41`) для медиа шлётся с `type:"PHOTO"` (вместо `"TEXT"`).

API: `MaxClient.send_photo(chat_id, path, caption="")` — делает все три шага;
`upload_photo(path) -> token` и `request_upload_url(count) -> url` — по отдельности.

**Видео/файлы/аудио** — по аналогии (другой `_type` и, вероятно, отдельный
upload-эндпоинт); пока не разобраны.

---

## Карта опкодов (снято с живого клиента 2026-09-01)

Опкод — 2 байта BE в заголовке. `dir` 0=запрос, 1=ответ. Разобранные:

| Opcode | Имя | Payload запроса | Ответ / назначение | Статус |
|-------:|-----|-----------------|--------------------|--------|
| 0x06 | HELLO | `{userAgent, deviceId}` | инициализация сессии | ✅ реализован |
| 0x13 | AUTH | `{token, …Sync}` | дамп аккаунта (profile/chats/contacts) | ✅ |
| 0x1b | STICKER_SYNC | `{type, sync}` | синк стикеров/реакций | — служебное |
| 0x1c | GET_STICKERS | `{type, ids}` | стикеры по id | — |
| 0x20 | CONTACTS | `{contactIds:[id]}` | `{contacts:[{id,names,phone}]}` — **резолв id→имя** | ✅ `resolve_contacts` |
| 0x23 | (presence) | `{contactIds:[id]}` | `{presence:{id:{seen,status}}}` — **онлайн-статус** (НЕ участники\!) | ✅ `get_presence` |
| 0x2e | SEARCH_PHONE | `{phone:"+7…"}` | `{contact:{…}}` — **поиск юзера по номеру** | ✅ `find_by_phone` |
| 0x30 | SYNC_CHAT | `{chatIds:[id]}` | `{chats:[…]}` — метаданные чатов | ✅ `sync_chats` |
| 0x31 | GET_HISTORY | `{chatId, from, forward, backward, getMessages}` | `{messages:[…]}` — история чата | ✅ `get_history` |
| 0x32 | MARK_READ | `{type:"READ_MESSAGE", chatId, messageId, mark}` | отметить прочитанным | ✅ `mark_read` |
| 0x43 | EDIT_MESSAGE | `{chatId, messageId, text, elements, attachments}` | редактировать своё сообщение | ✅ `edit_message` |
| 0x40 | SEND_MESSAGE | `{chatId, message, notify}` | отправка (текст/фото/формат) | ✅ `send_message/send_photo` |
| 0x41 | COMPOSING | `{chatId, type}` | «печатает» / pre-send | ✅ |
| 0x42 | DELETE_MESSAGE | `{chatId, messageIds, forMe}` | удаление (откат) | ✅ `delete_message` |
| 0x34 | DELETE_CHAT | `{chatId, lastEventTime, forAll}` | удалить/выйти из чата | ✅ `delete_chat` |
| 0x40 | (create group) | `message.attaches:[{_type:CONTROL, event:new, chatType:CHAT, title, userIds}]` | создать группу | ✅ `create_group` |
| 0x4b | SUBSCRIBE_CHAT | `{chatId, subscribe}` | подписка на live-апдейты чата | 〜 снят |
| 0x50 | REQUEST_UPLOAD | `{count}` | `{url}` — CDN для фото | ✅ `request_upload_url` |
| 0xa3 | CALL_HISTORY_SYNC | `{callHistorySync}` | история звонков | — |
| 0xd0 | (feed) | `{cursor, count}` | лента/сторис-превью | — |
| 0xd1 | (owners) | `{owners:[{type,id}]}` | инфо владельцев | — |
| 0x110 | FOLDER_SYNC | `{folderSync}` | папки чатов | — |
| 0x12e | BANNERS_SYNC | `{bannersSync}` | баннеры | — |

**Участники группы** берутся НЕ опкодом, а из map `participants` объекта чата
(ключи = user id) + резолв имён через 0x20 — см. `list_group_members`.

**Управление участниками** идёт через CONTROL-attach в SEND_MESSAGE (как create):
`event:new` = создать. add/remove member — тот же механизм с другим `event`
(точное имя события не снято — по запросу).

**Не разобрано (нужен доп. перехват):** create channel, add/remove member (event-имя),
pin/mute, постраничная подгрузка старых сообщений (from=время старейшего).
