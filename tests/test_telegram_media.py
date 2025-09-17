# tests/test_telegram_media.py

from typing import Any

from media_types import MediaItem
from telegram_media import iter_media_parts

# --- tiny fakes (duck-typed to match what iter_media_parts looks for) ---


class Obj:  # simple attribute bag
    def __init__(self, **kw):
        self.__dict__.update(kw)


def make_msg(**kw) -> Any:
    return Obj(**kw)


# ------------------------- tests -------------------------


def test_detect_photo():
    photo = Obj(file_unique_id="ph_u1", mime_type="image/jpeg")
    msg = make_msg(photo=photo)
    parts: list[MediaItem] = iter_media_parts(msg)
    assert len(parts) == 1
    m = parts[0]
    assert m.kind == "photo"
    assert m.unique_id == "ph_u1"
    assert m.mime == "image/jpeg"
    assert m.file_ref is photo


def test_detect_sticker_telethonish():
    # Telethon-style: msg.document with attributes[*].stickerset, alt/emoji/etc.
    stickerset = Obj(short_name="HotCherry")
    attr_sticker = Obj(stickerset=stickerset, alt="👋")
    doc = Obj(
        id=123, attributes=[attr_sticker], mime_type="image/webp", file_name="wave.webp"
    )
    msg = make_msg(document=doc)
    parts = iter_media_parts(msg)
    assert len(parts) == 1
    m = parts[0]
    assert m.kind == "sticker"
    assert m.unique_id == "123"
    assert m.mime == "image/webp"
    assert m.sticker_set == "HotCherry"
    assert m.sticker_name == "👋"
    assert m.file_ref is doc


def test_detect_sticker_botapi():
    # Bot API-style: msg.sticker with set_name/emoji
    st = Obj(
        file_unique_id="st_u2", set_name="HotCherry", emoji="😊", mime_type="image/webp"
    )
    msg = make_msg(sticker=st)
    parts = iter_media_parts(msg)
    assert len(parts) == 1
    m = parts[0]
    assert m.kind == "sticker"
    assert m.unique_id == "st_u2"
    assert m.sticker_set == "HotCherry"
    assert m.sticker_name == "😊"
    assert m.mime == "image/webp"
    assert m.file_ref is st


def test_detect_gif_and_animation():
    # GIF via document mime or animated attribute
    attr_anim = Obj()  # class name checked only; we can spoof via __class__.__name__
    attr_anim.__class__.__name__ = "DocumentAttributeAnimated"
    gif_doc = Obj(
        file_unique_id="gif_u3", mime_type="image/gif", attributes=[attr_anim]
    )
    msg_gif = make_msg(document=gif_doc)
    parts_gif = iter_media_parts(msg_gif)
    assert len(parts_gif) == 1 and parts_gif[0].kind == "gif"

    # Animation via Bot API-style field
    anim = Obj(file_unique_id="an_u4", mime_type="video/mp4")
    msg_anim = make_msg(animation=anim)
    parts_anim = iter_media_parts(msg_anim)
    assert len(parts_anim) == 1 and parts_anim[0].kind == "animation"
