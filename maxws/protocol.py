"""Кодек кадров WS-протокола MAX.

Формат кадра (снят с живой сессии, подтверждён на HELLO/auth/send):

    0       1  0x0A         магия/версия
    1       1  dir          0=запрос, 1=ответ
    2       2  seq (u16 BE) счётчик запросов; ответ echo-ит seq
    4       2  opcode (u16 BE)
    6       1  flags        0/1/2 (влияет на раскладку payload)
    7       3  length (u24 BE)
    10      N  payload      msgpack

Один бинарный WS-message = один такой кадр.
"""
from __future__ import annotations

from dataclasses import dataclass

MAGIC = 0x0A
DIR_REQUEST = 0x00
DIR_RESPONSE = 0x01
HEADER_LEN = 10


@dataclass(slots=True)
class Frame:
    opcode: int
    payload: bytes = b""
    seq: int = 0
    direction: int = DIR_REQUEST
    flags: int = 0x00
    data: object = None  # декодированный payload (заполняет клиент при приёме)

    def encode(self) -> bytes:
        if not (0 <= self.seq <= 0xFFFF):
            raise ValueError("seq out of u16 range")
        if not (0 <= self.opcode <= 0xFFFF):
            raise ValueError("opcode out of u16 range")
        n = len(self.payload)
        if n > 0xFFFFFF:
            raise ValueError("payload exceeds u24 length")
        return (
            bytes([MAGIC, self.direction])
            + self.seq.to_bytes(2, "big")
            + self.opcode.to_bytes(2, "big")
            + bytes([self.flags])
            + n.to_bytes(3, "big")
            + self.payload
        )

    @classmethod
    def decode(cls, data: bytes) -> "Frame":
        if len(data) < HEADER_LEN:
            raise ValueError("frame shorter than header")
        if data[0] != MAGIC:
            raise ValueError(f"bad magic 0x{data[0]:02x}")
        direction = data[1]
        seq = int.from_bytes(data[2:4], "big")
        opcode = int.from_bytes(data[4:6], "big")
        flags = data[6]
        length = int.from_bytes(data[7:10], "big")
        payload = data[HEADER_LEN:HEADER_LEN + length]
        if len(payload) != length:
            raise ValueError(f"payload truncated: got {len(payload)}, want {length}")
        return cls(opcode=opcode, payload=payload, seq=seq, direction=direction, flags=flags)
