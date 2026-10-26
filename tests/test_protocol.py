"""Верификация кодека кадров на публичном синтетическом трейсе.

Реальный live-трейс не включён из-за приватности. Fixture сохраняет форму
кадров; если кодек верен — decode(байты).encode() возвращает те же байты.
"""
from __future__ import annotations

import base64
import json
import pathlib

import pytest

from maxws.protocol import HEADER_LEN, MAGIC, Frame

TRACE = pathlib.Path(__file__).parent / "ws_trace.json"


def _binary_frames():
    frames = json.loads(TRACE.read_text())
    for f in frames:
        if f.get("dir") in ("sent", "recv") and f.get("b64"):
            b = base64.b64decode(f["b64"])
            # b64 в трейсе капнут на 12000 — берём только полные кадры
            length = int.from_bytes(b[7:10], "big")
            if len(b) == HEADER_LEN + length:
                yield f, b


def test_trace_has_frames():
    assert list(_binary_frames()), "в трейсе нет полных бинарных кадров"


def test_decode_roundtrip_on_trace_frames():
    checked = 0
    for meta, raw in _binary_frames():
        frame = Frame.decode(raw)
        assert frame.encode() == raw, f"round-trip разошёлся на op=0x{frame.opcode:02x}"
        checked += 1
    assert checked > 50, f"проверено слишком мало кадров: {checked}"


def test_header_fields_consistent():
    for meta, raw in _binary_frames():
        frame = Frame.decode(raw)
        assert raw[0] == MAGIC
        assert frame.direction in (0, 1)
        assert len(frame.payload) == int.from_bytes(raw[7:10], "big")


def test_send_opcode_present_in_trace():
    ops = {Frame.decode(raw).opcode for _, raw in _binary_frames()}
    assert 0x40 in ops, "в трейсе нет кадра отправки (0x40)"
    assert 0x23 in ops, "в трейсе нет кадра участников (0x23)"


def test_encode_manual_matches_layout():
    fr = Frame(opcode=0x40, payload=b"\x81\xa1a\x01", seq=0x0016, flags=0x02)
    raw = fr.encode()
    assert raw[:10] == bytes([MAGIC, 0x00]) + b"\x00\x16" + b"\x00\x40" + b"\x02" + b"\x00\x00\x04"
    assert Frame.decode(raw) == fr


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
