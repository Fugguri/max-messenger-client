"""HTML → MAX message `elements` (форматирование).

MAX кодирует форматирование как массив entity `{type, from, length}` — как в
Telegram. Смещения/длины в **символах** (code points), не байтах.

Подтверждён перехватом: `STRONG` (жирный). Остальные типы выведены по аналогии
и помечены TBD — уточнить перехватом Ctrl+I/U/… с живого клиента.
"""
from __future__ import annotations

from html.parser import HTMLParser

# HTML-тег → тип элемента MAX. STRONG подтверждён; ниже — TBD.
TAG_TYPE = {
    "b": "STRONG", "strong": "STRONG",
    "i": "EMPHASIZED", "em": "EMPHASIZED",          # TBD
    "u": "UNDERLINE",                                # TBD
    "s": "STRIKETHROUGH", "strike": "STRIKETHROUGH", "del": "STRIKETHROUGH",  # TBD
    "code": "MONOSPACE", "pre": "MONOSPACE",         # TBD
}
_NEWLINE_TAGS = {"br", "p", "div"}


class _Collector(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._chunks: list[str] = []
        self._open: list[tuple[str, int]] = []   # (type, start_offset)
        self.elements: list[dict] = []

    @property
    def _pos(self) -> int:
        return sum(len(c) for c in self._chunks)

    def handle_starttag(self, tag, attrs):
        t = TAG_TYPE.get(tag.lower())
        if t:
            self._open.append((t, self._pos))
        elif tag.lower() == "br":
            self._chunks.append("\n")

    def handle_endtag(self, tag):
        t = TAG_TYPE.get(tag.lower())
        if t:
            for i in range(len(self._open) - 1, -1, -1):
                if self._open[i][0] == t:
                    typ, start = self._open.pop(i)
                    length = self._pos - start
                    if length > 0:
                        self.elements.append({"type": typ, "from": start, "length": length})
                    break
        elif tag.lower() in _NEWLINE_TAGS:
            self._chunks.append("\n")

    def handle_data(self, data):
        self._chunks.append(data)

    def text(self) -> str:
        return "".join(self._chunks)


def html_to_message(html: str) -> tuple[str, list[dict]]:
    """HTML → (plain_text, elements). elements отсортированы по `from`.

    >>> html_to_message("<b>жирный</b> обычный")
    ('жирный обычный', [{'type': 'STRONG', 'from': 0, 'length': 6}])
    """
    c = _Collector()
    c.feed(html)
    return c.text(), sorted(c.elements, key=lambda e: (e["from"], e["length"]))
