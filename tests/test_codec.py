"""Тесты msgpack-слоя: кодировка id, сборка payload'ов, разбор кадров из трейса."""
from __future__ import annotations

import base64
import json
import pathlib

import msgpack
import pytest

from maxws import codec
from maxws.codec import Id
from maxws.protocol import Frame

TRACE = pathlib.Path(__file__).parent / "ws_trace.json"


def test_id_roundtrip():
    for value in (0, 1, 123456789, 987654321, 2**63):
        packed = codec.pack(Id(value))
        back = codec.unpack_all(packed)[0]
        assert isinstance(back, Id) and int(back) == value


def test_id_ext_wire_format():
    # ext type 1, data = 0xCF + uint64(8, BE)
    raw = codec.pack(Id(0x17EB2BE9))
    ext = msgpack.unpackb(raw, raw=False)
    assert ext.code == 1
    assert ext.data == b"\xcf" + (0x17EB2BE9).to_bytes(8, "big")


def test_build_send_shape():
    payload = codec.build_send(chat_id=123, text="привет", cid=999)
    m = codec.unpack_map(payload)
    assert int(m["chatId"]) == 123
    assert m["message"]["text"] == "привет"
    assert m["message"]["cid"] == 999
    assert m["message"]["elements"] == [] and m["message"]["attaches"] == []
    assert m["notify"] is True


def test_build_get_members_and_composing():
    assert codec.unpack_map(codec.build_get_members(7))["contactIds"][0] == Id(7)
    comp = codec.unpack_map(codec.build_composing(7))
    assert int(comp["chatId"]) == 7 and comp["type"] == "TEXT"


def _payload_by_opcode(op: int):
    frames = json.loads(TRACE.read_text())
    for f in frames:
        if f.get("dir") == "sent" and f.get("b64"):
            fr = Frame.decode(base64.b64decode(f["b64"]))
            if fr.opcode == op and fr.flags == 0x00:
                return fr.payload
    return None


def test_decode_trace_flag0_payloads():
    # flags=0 кадры (участники/composing/sync) — чистый один map
    p = _payload_by_opcode(0x23)  # GET_MEMBERS
    assert p is not None
    assert "contactIds" in codec.unpack_map(p)

    p = _payload_by_opcode(0x41)  # COMPOSING
    if p is not None:
        m = codec.unpack_map(p)
        assert m.get("type") == "TEXT" and "chatId" in m


def test_id_roundtrip_negative_and_large():
    # id групп ОТРИЦАТЕЛЬНЫЕ, id сообщений — большие; кодек не должен падать
    for v in (0, 123456, -12345678901234, 987654321012345678):
        back = codec.unpack_all(codec.pack(Id(v)))[0]
        assert isinstance(back, Id) and int(back) == v


def test_id_negative_uses_int64_marker():
    # отрицательный → msgpack int64 (0xd3); неотрицательный → uint64 (0xcf)
    ext_neg = codec.pack(Id(-5))
    ext_pos = codec.pack(Id(5))
    assert b"\xd3" in ext_neg
    assert b"\xcf" in ext_pos


def test_msg_search_phone():
    assert codec.msg_search_phone("+79161234567") == {"phone": "+79161234567"}


def test_msg_resolve_contacts():
    obj = codec.msg_resolve_contacts([1, -2, 3])
    assert [int(x) for x in obj["contactIds"]] == [1, -2, 3]
    assert all(isinstance(x, Id) for x in obj["contactIds"])


def test_opcodes_verified():
    from maxws import opcodes
    assert opcodes.GET_HISTORY == 0x31
    assert opcodes.MARK_READ == 0x32
    assert opcodes.DELETE_CHAT == 0x34
    assert opcodes.EDIT_MESSAGE == 0x43
    assert opcodes.SEARCH_PHONE == 0x2E
    assert opcodes.name(0x34) == "DELETE_CHAT"


def test_dm_chat_id_xor():
    from maxws import MaxClient
    c = MaxClient("tok")
    c.session = {"profile": {"contact": {"id": Id(123456)}}}
    assert c.dm_chat_id(111) == 123456 ^ 111      # другой пользователь
    assert c.dm_chat_id(123456) == 0              # себе → Избранное (X^X=0)

if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
