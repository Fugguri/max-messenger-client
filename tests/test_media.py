"""Тесты медиа-слоя (фото): attach-билдеры, multipart, разбор ответа CDN.

Схема разобрана перехватом web.max.ru (2026-08-31), флоу проверен вживую:
REQUEST_UPLOAD(0x50){count} → {url} → POST file → {photos:{id:{token}}} → SEND attaches[PHOTO].
"""

from __future__ import annotations

import pytest

from maxmessenger import codec, media, opcodes


def test_request_upload_opcode():
    assert opcodes.REQUEST_UPLOAD == 0x50
    assert opcodes.name(0x50) == "REQUEST_UPLOAD"


def test_msg_request_upload():
    assert codec.msg_request_upload(1) == {"count": 1}
    assert codec.msg_request_upload(3) == {"count": 3}


def test_photo_attach():
    assert codec.photo_attach("TOK") == {"_type": "PHOTO", "photoToken": "TOK"}


def test_send_with_photo_attach_shape():
    """SEND с фото: message.attaches несёт PHOTO с токеном (как в живом кадре)."""
    obj = codec.msg_send(0, "", 111, attaches=[codec.photo_attach("TOK")])
    att = obj["message"]["attaches"]
    assert att == [{"_type": "PHOTO", "photoToken": "TOK"}]
    assert int(obj["chatId"]) == 0


def test_multipart_field_and_boundary():
    body, boundary = media._multipart("file", "up.png", b"\x89PNG\x00", "image/png")
    assert boundary in body.decode("latin1")
    assert 'name="file"; filename="up.png"' in body.decode("latin1")
    assert b"Content-Type: image/png" in body
    assert b"\x89PNG\x00" in body
    assert body.rstrip().endswith(f"--{boundary}--".encode())


def test_extract_photo_token_ok():
    resp = {"photos": {"ABC==": {"token": "eQNnjTube..."}}}
    assert media.extract_photo_token(resp) == "eQNnjTube..."


def test_extract_photo_token_errors():
    with pytest.raises(ValueError):
        media.extract_photo_token({})
    with pytest.raises(ValueError):
        media.extract_photo_token({"photos": {"ABC==": {}}})


def test_guess_content_type():
    assert media.guess_content_type("x.png") == "image/png"
    assert media.guess_content_type("x.jpg") == "image/jpeg"
