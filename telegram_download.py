# telegram_download.py

from typing import Any
import asyncio
import io
import os

async def download_media_bytes(client: Any, file_ref: Any) -> bytes:
    """
    Download media bytes from Telegram in a duck-typed way.
    Prefers in-memory buffers (BytesIO) to avoid empty files or unknown paths.
    """
    # 1) Try download_media into a BytesIO buffer
    dm = getattr(client, "download_media", None)
    if callable(dm):
        buf = io.BytesIO()
        res = dm(file_ref, file=buf)  # many clients accept file-like target
        if asyncio.iscoroutine(res):
            res = await res

        # Some SDKs return the buffer, some return None and just write into it.
        if isinstance(res, (bytes, bytearray)):
            return bytes(res)
        if isinstance(res, io.BytesIO):
            return res.getvalue()

        # If nothing returned, but buffer has content, use it.
        data = buf.getvalue()
        if data:
            return data

        # Some SDKs return a filesystem path; read it back if so.
        if isinstance(res, str) and os.path.exists(res):
            with open(res, "rb") as f:
                return f.read()

    # 2) Try download_file into a BytesIO buffer
    df = getattr(client, "download_file", None)
    if callable(df):
        buf = io.BytesIO()
        res = df(file_ref, file=buf)  # many clients accept file-like target
        if asyncio.iscoroutine(res):
            res = await res

        # Some SDKs return bytes; otherwise read from buffer.
        if isinstance(res, (bytes, bytearray)):
            return bytes(res)

        data = buf.getvalue()
        if data:
            return data

        # As above, if a path string was returned, read it.
        if isinstance(res, str) and os.path.exists(res):
            with open(res, "rb") as f:
                return f.read()

    raise NotImplementedError("No supported download method on client for media bytes")
