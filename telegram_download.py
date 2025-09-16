# telegram_download.py

from typing import Any
import asyncio

async def download_media_bytes(client: Any, file_ref: Any) -> bytes:
    """
    Download media bytes from Telegram in a duck-typed way.
    - If the client exposes an async 'download_media' returning bytes, use it.
    - Else if it exposes 'download_file' or similar, try that.
    - Otherwise raise NotImplementedError.

    This function is intentionally generic so we don't wire to a specific SDK yet.
    It is NOT used unless the media feature is enabled.
    """
    # Prefer: client.download_media(file_ref) -> bytes
    dm = getattr(client, "download_media", None)
    if callable(dm):
        res = dm(file_ref)
        if asyncio.iscoroutine(res):
            return await res
        if isinstance(res, (bytes, bytearray)):
            return bytes(res)

    # Fallbacks commonly seen in SDKs
    df = getattr(client, "download_file", None)
    if callable(df):
        res = df(file_ref)
        if asyncio.iscoroutine(res):
            data = await res
        else:
            data = res
        if isinstance(data, (bytes, bytearray)):
            return bytes(data)

    raise NotImplementedError("No supported download method on client for media bytes")
