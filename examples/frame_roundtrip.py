"""Пример: собрать и разобрать кадр отправки (работает без сети)."""
from maxws import Frame, codec
from maxws.codec import Id

payload = codec.build_send(chat_id=123456789, text="привет из maxws", cid=1234567890)
raw = Frame(opcode=0x40, payload=payload, seq=1, flags=0x02).encode()
print("кадр (hex):", raw[:16].hex(" "), "…", f"({len(raw)} байт)")

frame = Frame.decode(raw)
print("opcode:", hex(frame.opcode), "seq:", frame.seq, "flags:", frame.flags)
print("payload:", codec.unpack_map(frame.payload))
assert int(codec.unpack_map(frame.payload)["chatId"]) == 123456789
print("ok")
