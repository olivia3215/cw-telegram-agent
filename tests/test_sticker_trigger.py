# test_sticker_trigger.py

import sticker_trigger as st


def test_nominal_no_reply():
    text = "# «sticker»\n\nWendyAI\n😀\n"
    trig = st.parse_first_sticker_trigger(text)
    assert trig is not None
    assert trig.set_short_name == "WendyAI"
    assert trig.sticker_name == "😀"
    assert trig.reply_to_message_id is None


def test_with_reply():
    text = "# «sticker» 1234\n\nWendyAI\n😘\n"
    trig = st.parse_first_sticker_trigger(text)
    assert trig is not None
    assert trig.set_short_name == "WendyAI"
    assert trig.sticker_name == "😘"
    assert trig.reply_to_message_id == 1234


def test_extra_blank_lines_and_whitespace():
    text = "# «sticker» 7\n\n\n   WendyAI   \n   😎   \n\n"
    trig = st.parse_first_sticker_trigger(text)
    assert trig is not None
    assert trig.set_short_name == "WendyAI"
    assert trig.sticker_name == "😎"
    assert trig.reply_to_message_id == 7


def test_multiple_blocks_first_wins():
    text = (
        "# «sticker»\n\nWendyAI\n😀\n"
        "\nSome text in between\n"
        "# «sticker» 42\n\nWendyAI\n😘\n"
    )
    trig = st.parse_first_sticker_trigger(text)
    assert trig is not None
    assert trig.set_short_name == "WendyAI"
    assert trig.sticker_name == "😀"
    assert trig.reply_to_message_id is None


def test_negative_ascii_without_guillemets():
    text = "# sticker\n\nWendyAI\n😀\n"
    trig = st.parse_first_sticker_trigger(text)
    assert trig is None


def test_negative_missing_name_even_with_set():
    # Header + set line but no name line → invalid unless we were in the old single-line mode,
    # which we're not here.
    text = "# «sticker»\n\nWendyAI\n"
    trig = st.parse_first_sticker_trigger(
        text, allow_missing_set_during_transition=False
    )
    assert trig is None
