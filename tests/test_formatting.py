"""Тесты HTML → MAX elements (совпадение с реальным кадром: STRONG from/length)."""

from maxmessenger.formatting import html_to_message


def test_bold_matches_live_frame():
    # живой кадр: text='ЖИРНЫЙТЕКСТ', elements=[{STRONG,0,11}]
    text, els = html_to_message("<b>ЖИРНЫЙТЕКСТ</b>")
    assert text == "ЖИРНЫЙТЕКСТ"
    assert els == [{"type": "STRONG", "from": 0, "length": 11}]


def test_offsets_in_characters():
    text, els = html_to_message("обычный <b>жирный</b> хвост")
    assert text == "обычный жирный хвост"
    assert els == [{"type": "STRONG", "from": 8, "length": 6}]  # from/length в символах


def test_multiple_and_nested():
    text, els = html_to_message("<b>жир</b> и <i>кур</i>")
    assert text == "жир и кур"
    types = {e["type"] for e in els}
    assert types == {"STRONG", "EMPHASIZED"}
    assert all(e["length"] == 3 for e in els)


def test_br_becomes_newline():
    text, els = html_to_message("строка1<br>строка2")
    assert text == "строка1\nстрока2"


def test_plain_text_no_elements():
    text, els = html_to_message("просто текст")
    assert text == "просто текст" and els == []
