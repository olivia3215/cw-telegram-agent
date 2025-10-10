# tests/test_telegram_media.py

# Copyright (c) 2025 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.

from typing import Any

from media.media_types import MediaItem
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
    assert m.sticker_set_name == "HotCherry"
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
    assert m.sticker_set_name == "HotCherry"
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


def test_detect_video_and_animated_sticker():
    # Regular video via document mime type
    video_doc = Obj(file_unique_id="vid_u5", mime_type="video/mp4")
    msg_video = make_msg(document=video_doc)
    parts_video = iter_media_parts(msg_video)
    assert len(parts_video) == 1 and parts_video[0].kind == "video"
    assert parts_video[0].unique_id == "vid_u5"
    assert parts_video[0].mime == "video/mp4"

    # WebM video
    webm_doc = Obj(file_unique_id="webm_u6", mime_type="video/webm")
    msg_webm = make_msg(document=webm_doc)
    parts_webm = iter_media_parts(msg_webm)
    assert len(parts_webm) == 1 and parts_webm[0].kind == "video"
    assert parts_webm[0].mime == "video/webm"

    # Animated sticker (TGS file) - gzip-compressed Lottie
    tgs_doc = Obj(file_unique_id="tgs_u7", mime_type="application/gzip")
    msg_tgs = make_msg(document=tgs_doc)
    parts_tgs = iter_media_parts(msg_tgs)
    # TGS files are now classified as "sticker" kind (MIME type distinguishes animated from static)
    assert len(parts_tgs) == 1 and parts_tgs[0].kind == "sticker"
    assert parts_tgs[0].is_animated_sticker()  # Helper method to check if it's animated
    assert parts_tgs[0].unique_id == "tgs_u7"
    assert parts_tgs[0].mime == "application/gzip"

    # Video with DocumentAttributeVideo
    attr_video = Obj()
    attr_video.__class__.__name__ = "DocumentAttributeVideo"
    video_attr_doc = Obj(
        file_unique_id="vid_attr_u8",
        mime_type="video/quicktime",
        attributes=[attr_video],
    )
    msg_video_attr = make_msg(document=video_attr_doc)
    parts_video_attr = iter_media_parts(msg_video_attr)
    assert len(parts_video_attr) == 1 and parts_video_attr[0].kind == "video"
    assert parts_video_attr[0].mime == "video/quicktime"


def test_animated_sticker_not_duplicated():
    """
    Test that animated stickers (TGS) with both DocumentAttributeSticker and gzip MIME
    are only added once, not duplicated.

    This was a bug where TGS stickers were added twice:
    - Once by _maybe_add_sticker (due to DocumentAttributeSticker)
    - Once by _maybe_add_gif_or_animation (due to application/gzip MIME type)
    """
    # Create a TGS sticker as it comes from Telegram: with both stickerset attribute and gzip MIME
    stickerset = Obj(short_name="Lamplover")
    attr_sticker = Obj(stickerset=stickerset, alt="😂")
    tgs_doc = Obj(
        file_unique_id="tgs_with_stickerset",
        mime_type="application/gzip",
        attributes=[attr_sticker],
    )
    msg_tgs = make_msg(document=tgs_doc)
    parts = iter_media_parts(msg_tgs)

    # Should only have ONE sticker part, not two
    assert len(parts) == 1
    assert parts[0].kind == "sticker"
    assert parts[0].is_animated_sticker()
    assert parts[0].sticker_set_name == "Lamplover"
    assert parts[0].sticker_name == "😂"
    assert parts[0].mime == "application/gzip"
