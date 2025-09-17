# tests/test_media_format.py

import pytest
from media_format import format_media_description, format_sticker_sentence


def test_format_media_description_with_text():
    out = format_media_description("A sunny beach with umbrellas")
    assert out == "that appears as ‹A sunny beach with umbrellas›"
    assert "‹" in out and "›" in out


@pytest.mark.parametrize("raw", [None, "", "   "])
def test_format_media_description_missing_or_blank(raw):
    out = format_media_description(raw)  # type: ignore[arg-type]
    assert out == "that is not understood"
    assert "‹" not in out and "›" not in out


@pytest.mark.parametrize("raw", [
    "not understood",
    "Not Understood: format gif",
    "sticker not understood (format tgs)",
    "Sticker Not Understood (FORMAT TGS)",
])
def test_format_media_description_not_understood_variants(raw):
    out = format_media_description(raw)
    assert out == "that is not understood"
    assert "‹" not in out and "›" not in out


def test_format_media_description_trims_whitespace():
    out = format_media_description("  hello  ")
    assert out == "that appears as ‹hello›"


def test_format_sticker_sentence_with_desc():
    out = format_sticker_sentence("😊", "HotCherry", "Kermit gives a thumbs up")
    assert out == "the sticker '😊' from the sticker set 'HotCherry' that appears as ‹Kermit gives a thumbs up›"


@pytest.mark.parametrize("desc", ["", "   ", "not understood", "sticker not understood (format tgs)"])
def test_format_sticker_sentence_without_desc(desc):
    out = format_sticker_sentence("👋", "WendyDancer", desc)
    assert out == "the sticker '👋' from the sticker set 'WendyDancer' that is not understood"
