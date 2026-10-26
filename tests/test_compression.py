"""LZ4-сжатие payload: распаковка flags=2 кадра из трейса + round-trip."""

from __future__ import annotations

import base64
import json
import pathlib

from maxmessenger import codec
from maxmessenger.codec import Id
from maxmessenger.protocol import Frame

TRACE = pathlib.Path(__file__).parent / "ws_trace.json"


def _trace_send_frame() -> Frame:
    for f in json.loads(TRACE.read_text()):
        if f.get("dir") == "sent" and f.get("b64"):
            fr = Frame.decode(base64.b64decode(f["b64"]))
            if fr.opcode == 0x40 and b"WSPROBE" in fr.payload:
                return fr
    raise AssertionError("нет send-кадра в трейсе")


def test_trace_frame_is_compressed():
    fr = _trace_send_frame()
    assert fr.flags > 0, "живой send-кадр должен быть сжат (flags>0)"


def test_decode_trace_compressed_payload():
    fr = _trace_send_frame()
    obj = codec.decode_payload(fr.payload, fr.flags)
    assert obj["chatId"] == Id(0)  # Избранное
    assert obj["message"]["text"] == "WSPROBE_MARKER_777"
    assert obj["notify"] is True


def test_encode_roundtrip_compressed():
    # большой объект → сжимается (flags>0) → распаковывается обратно
    obj = codec.msg_send(123456789, "текст " * 30, cid=123)
    payload, flags = codec.encode_payload(obj)
    assert flags > 0, "крупный payload должен сжаться"
    back = codec.decode_payload(payload, flags)
    assert back["message"]["text"] == "текст " * 30
    assert back["chatId"] == Id(123456789)


def test_small_payload_not_compressed():
    payload, flags = codec.encode_payload({"a": 1})
    assert flags == 0
    assert codec.decode_payload(payload, flags) == {"a": 1}
