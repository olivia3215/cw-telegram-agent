# llm.py

import asyncio
import base64
import json
import logging
import os
from abc import ABC, abstractmethod
from collections.abc import Iterable
from typing import Any, TypedDict
from urllib import error, request

import google.generativeai as genai
import httpx
from openai import AsyncOpenAI

logger = logging.getLogger(__name__)


# --- Role-structured prompt builder for Gemini (pure helper; parts-aware) ---


# --- Role-structured prompt builder for Gemini (parts-aware, sender_id, open media kinds) ---


# Each message can have multiple parts in the original order (text, media renderings, etc.)
class MsgTextPart(TypedDict):
    kind: str  # must be "text"
    text: str  # plain text chunk


class MsgMediaPart(TypedDict, total=False):
    kind: str  # must be "media"
    # Open-ended media kind (e.g., "sticker", "photo", "video", "animated_sticker", "audio", "music", ...)
    media_kind: str | None
    # Your already-rendered description string (preferred)
    rendered_text: str | None
    # Optional metadata (for trace/fallbacks)
    unique_id: str | None
    set_name: str | None
    sticker_name: str | None


MsgPart = MsgTextPart | MsgMediaPart


class ChatMsg(TypedDict, total=False):
    """
    Normalized view of a chat message for building LLM history.

    Content (one of):
      - parts: list[MsgPart]  (preferred)
      - text: str             (fallback if 'parts' missing)

    Identity / trace:
      - sender:    display name
      - sender_id: stable unique sender id (e.g., Telegram user id)
      - msg_id:    message id string (if available)
      - is_agent:  True if this message was sent by *our* agent persona
      - ts_iso:    optional ISO-8601 timestamp (trace only; not shown to model)
    """

    sender: str
    sender_id: str
    parts: list[MsgPart]
    text: str
    is_agent: bool
    msg_id: str | None
    ts_iso: str | None


def _mk_text_part(text: str) -> dict[str, str]:
    return {"text": text}


def _normalize_parts_for_message(
    m: ChatMsg,
    *,
    include_speaker_prefix: bool,
    include_message_ids: bool,
    is_agent: bool,
) -> list[dict[str, str]]:
    """
    Produce the sequence of Gemini text parts for a single message:
      - Leading metadata header part (From / sender_id / msg id), even in DMs.
      - Then each original message part in order (text or rendered media).
      - If a media part lacks 'rendered_text', emit a succinct placeholder so the model
        knows media was present.
    """
    parts: list[dict[str, str]] = []

    # 1) Metadata header (always, per spec)
    if not is_agent:
        header_bits: list[str] = []
        if include_speaker_prefix:
            who = m.get("sender") or ""
            sid = m.get("sender_id") or ""
            if who and sid:
                header_bits.append(f"From: {who} ({sid})")
            elif who or sid:
                header_bits.append(f"From: {who or sid}")
        if include_message_ids and m.get("msg_id"):
            header_bits.append(f"id: {m['msg_id']}")
        if header_bits:
            parts.append(_mk_text_part(" — ".join(header_bits)))

    # 2) Original message content in original order
    raw_parts: list[MsgPart] | None = m.get("parts")

    if raw_parts is not None and len(raw_parts) > 0:
        for p in raw_parts:
            k = (p.get("kind") or "").lower()
            if k == "text":
                txt = (p.get("text") or "").strip()
                if txt:
                    parts.append(_mk_text_part(txt))
            elif k == "media":
                rendered = (p.get("rendered_text") or "").strip()
                if rendered:
                    parts.append(_mk_text_part(rendered))
                else:
                    # Fallback: brief placeholder so the LLM knows something was here.
                    mk = (p.get("media_kind") or "media").strip()
                    uid = (p.get("unique_id") or "").strip()
                    placeholder = f"[{mk} present" + (f" uid={uid}]" if uid else "]")
                    parts.append(_mk_text_part(placeholder))
            else:
                # Unknown part type: surface minimally instead of dropping.
                parts.append(_mk_text_part(f"[{k or 'unknown'} part]"))
    else:
        # Fallback: single text
        fallback = (m.get("text") or "").strip()
        if fallback:
            parts.append(_mk_text_part(fallback))

    return parts


def build_gemini_contents(
    *,
    # System turn inputs
    persona_instructions: str,
    role_prompt: str | None,
    llm_specific_prompt: str | None,
    now_iso: str,
    chat_type: str,  # "direct" | "group" (stringly typed to avoid import cycles)
    curated_stickers: Iterable[str] | None = None,
    # History & target
    history: Iterable[ChatMsg],
    target_message: ChatMsg | None,  # message we want the model to respond to
    history_size: int = 500,
    # Formatting toggles
    include_speaker_prefix: bool = True,
    include_message_ids: bool = True,
) -> list[dict[str, Any]]:
    """
    Construct Gemini 'contents' with roles and multi-part messages:
      - One 'system' turn: persona + role prompt + model-specific prompt + metadata
      - Chronological 'user'/'assistant' turns for prior messages (bounded by history_size),
        each with an ordered list of 'parts' (metadata header first, then content parts).
      - Final 'user' turn for the target message (appended last), also parts-based.

    Pure function: no I/O, no network, no mutation of inputs.
    """
    # --- 1) System turn ---
    sys_lines: list[str] = []
    if persona_instructions:
        sys_lines.append(persona_instructions.strip())
    if role_prompt:
        sys_lines.append("\n# Role Prompt\n" + role_prompt.strip())
    if llm_specific_prompt:
        sys_lines.append("\n# Model-Specific Guidance\n" + llm_specific_prompt.strip())
    sys_lines.append(f"\n# Context\nCurrent time: {now_iso}\nChat type: {chat_type}")
    if curated_stickers:
        sticker_list = ", ".join(curated_stickers)
        sys_lines.append(f"Curated stickers available: {sticker_list}")

    contents: list[dict[str, Any]] = [
        {"role": "system", "parts": [_mk_text_part("\n\n".join(sys_lines).strip())]}
    ]

    # --- 2) Chronological history (bounded) ---
    hist_list = list(history)
    if history_size >= 0:
        hist_list = hist_list[-history_size:]

    for m in hist_list:
        is_agent = bool(m.get("is_agent"))
        role = "assistant" if is_agent else "user"
        parts = _normalize_parts_for_message(
            m,
            include_speaker_prefix=include_speaker_prefix,
            include_message_ids=include_message_ids,
            is_agent=is_agent,
        )
        if parts:
            contents.append({"role": role, "parts": parts})

    # --- 3) Target message appended last (if provided) ---
    if target_message is not None:
        tm_parts = _normalize_parts_for_message(
            target_message,
            include_speaker_prefix=include_speaker_prefix,
            include_message_ids=include_message_ids,
            is_agent=False,
        )
        if tm_parts:
            contents.append({"role": "user", "parts": tm_parts})
    return contents


class LLM(ABC):
    prompt_name: str = "Default"

    @abstractmethod
    async def query(self, system_prompt: str, user_prompt: str) -> str:
        pass


class ChatGPT(LLM):
    prompt_name = "ChatGPT"

    def __init__(
        self,
        api_key: str | None = None,
        model: str = "gpt-4.1-nano",
        temperature: float = 0.7,
    ):
        self.api_key = api_key or os.getenv("OPENAI_API_KEY")
        if not self.api_key:
            raise ValueError(
                "Missing ChatGPT API key. Set OPENAI_API_KEY or pass it explicitly."
            )
        self.client = AsyncOpenAI(api_key=api_key)
        self.model = model
        self.temperature = temperature
        self.history_size = 120

    async def query(self, system_prompt: str, user_prompt: str) -> str:
        logger.debug(f"Querying ChatGPT with '{system_prompt}' and '{user_prompt}'")
        response = await self.client.chat.completions.create(
            model=self.model,
            temperature=self.temperature,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            n=1,
        )

        if not response.choices:
            raise RuntimeError("LLM returned no choices.")

        return response.choices[0].message.content.strip()


class OllamaLLM(LLM):
    def __init__(self, base_url="http://serv:11434", model="gemma3"):
        self.prompt_name = model
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.history_size = 5

    async def query(self, system: str, user: str) -> str:
        url = f"{self.base_url}/api/chat"
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "stream": False,
        }

        async with httpx.AsyncClient() as client:
            response = await client.post(url, json=payload, timeout=60)
            response.raise_for_status()
            data = response.json()
        return data.get("message", {}).get("content", "")


class GeminiLLM(LLM):
    prompt_name = "Gemini"

    def __init__(
        self,
        model: str = "gemini-2.5-flash-preview-09-2025",
        api_key: str | None = None,
    ):
        self.model_name = model
        self.api_key = api_key or os.getenv("GOOGLE_GEMINI_API_KEY")
        if not self.api_key:
            raise ValueError(
                "Missing Gemini API key. Set GOOGLE_GEMINI_API_KEY or pass it explicitly."
            )
        genai.configure(api_key=self.api_key)
        self.model = genai.GenerativeModel(model)
        self.history_size = 500

    async def query(self, system_prompt: str, user_prompt: str) -> str:
        full_prompt = f"{system_prompt}\n\n{user_prompt}"
        logger.warning(f"=====> prompt: {full_prompt}")
        response = await asyncio.to_thread(self.model.generate_content, full_prompt)
        logger.warning(f"=====> response: {response}")
        return response.text

    IMAGE_DESCRIPTION_PROMPT = (
        "You are given a single image. Describe the scene in rich detail so a reader "
        "can understand it without seeing the image. Include salient objects, colors, "
        "relations, actions, and setting. Output only the description."
    )

    def describe_image(self, image_bytes: bytes, mime_type: str | None = None) -> str:
        """
        Return a rich, single-string description for the given image.
        Uses Gemini via REST with this instance's api key.
        Raises on failures so the scheduler's retry policy can handle it.
        """
        if not self.api_key:
            raise ValueError("Missing Gemini API key (GOOGLE_GEMINI_API_KEY)")

        # Minimal mime sniffing; refine later if needed.
        if not mime_type:
            mime_type = "image/jpeg"
            if image_bytes.startswith(b"\x89PNG"):
                mime_type = "image/png"
            elif image_bytes[:3] == b"GIF":
                mime_type = "image/gif"
            elif image_bytes[:4] == b"RIFF" and image_bytes[8:12] == b"WEBP":
                mime_type = "image/webp"

        # Prefer a vision-capable model
        model = "gemini-2.5-flash-preview-09-2025"

        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={self.api_key}"

        payload = {
            "contents": [
                {
                    "role": "user",
                    "parts": [
                        {"text": self.IMAGE_DESCRIPTION_PROMPT},
                        {
                            "inline_data": {
                                "mime_type": mime_type,
                                "data": base64.b64encode(image_bytes).decode("ascii"),
                            }
                        },
                    ],
                }
            ]
        }

        data = json.dumps(payload).encode("utf-8")
        req = request.Request(
            url, data=data, headers={"Content-Type": "application/json"}
        )

        try:
            with request.urlopen(req, timeout=30) as resp:
                body = resp.read()
        except error.HTTPError as e:
            raise RuntimeError(
                f"Gemini HTTP {e.code}: {e.read().decode('utf-8', 'ignore')}"
            ) from e
        except Exception as e:
            raise RuntimeError(f"Gemini request failed: {e}") from e

        try:
            obj = json.loads(body.decode("utf-8"))
            # Typical path: candidates[0].content.parts[0].text
            candidates = obj.get("candidates") or []
            if not candidates:
                raise RuntimeError(f"Gemini returned no candidates: {obj}")
            parts = (candidates[0].get("content") or {}).get("parts") or []
            if not parts or "text" not in parts[0]:
                raise RuntimeError(f"Gemini returned no text parts: {obj}")
            return parts[0]["text"].strip()
        except Exception as e:
            raise RuntimeError(f"Gemini parse error: {e}") from e

    async def _generate_with_contents(
        self,
        *,
        contents: list[dict[str, object]],
        model: str | None = None,
        timeout_s: float | None = None,
        system_instruction: str | None = None,
    ) -> str:
        """
        Thin wrapper around the Gemini client for role-structured 'contents'.
        Sends ONLY user/assistant turns in 'contents'. If provided, 'system_instruction'
        is passed via the model config path (never mixed into message contents).
        Returns the model's text ('' on no text). No internal retries.
        """
        try:
            gm = getattr(self, "model", None)
            if gm is None:
                raise RuntimeError("Gemini model not initialized")

            # Normalize roles for Gemini: assistant -> model; only "user" and "model" allowed.
            try:
                contents_norm: list[dict[str, object]] = []
                for turn in contents:
                    role = turn.get("role")
                    if role == "assistant":
                        mapped_role = "model"
                    elif role == "user":
                        mapped_role = "user"
                    else:
                        # Be conservative: anything unexpected becomes "user"
                        mapped_role = "user"
                    parts = turn.get("parts") or []
                    contents_norm.append({"role": mapped_role, "parts": parts})
            except Exception:
                contents_norm = contents

            # Prefer passing system_instruction directly (newer google-genai supports it).
            response = None
            if system_instruction:
                try:
                    response = await asyncio.to_thread(
                        gm.generate_content,
                        contents_norm,
                        system_instruction=system_instruction,
                    )
                except TypeError:
                    # Older SDKs: construct a temporary GenerativeModel with system_instruction.
                    try:
                        model_name = getattr(gm, "model", None) or getattr(
                            self, "model_name", None
                        )
                        if not model_name:
                            raise RuntimeError(
                                "No Gemini model name available for re-instantiation"
                            )
                        gm2 = gm.__class__(
                            model_name,
                            system_instruction=system_instruction,
                            safety_settings=getattr(self, "safety_settings", None),
                            generation_config=getattr(self, "generation_config", None),
                        )
                        response = await asyncio.to_thread(
                            gm2.generate_content, contents_norm
                        )
                    except Exception:
                        # If we cannot set system_instruction without mixing content, give up on it
                        # and proceed without any system instruction (do NOT fold into contents).
                        response = await asyncio.to_thread(
                            gm.generate_content, contents
                        )
            else:
                # No system instruction: normal call.
                response = await asyncio.to_thread(gm.generate_content, contents_norm)

            # Extract the first candidate's text safely
            text = ""
            if response is not None:
                if hasattr(response, "text") and isinstance(response.text, str):
                    text = response.text
                elif hasattr(response, "candidates") and response.candidates:
                    cand = response.candidates[0]
                    t = getattr(cand, "text", None)
                    if isinstance(t, str):
                        text = t or ""
                    else:
                        content = getattr(cand, "content", None)
                        if content and getattr(content, "parts", None):
                            first_part = content.parts[0]
                            if isinstance(first_part, dict) and "text" in first_part:
                                text = str(first_part["text"] or "")

            return text or ""
        except Exception as e:
            logger.error("SDK exception: %s", e)
            return ""

    async def query_structured(
        self,
        *,
        persona_instructions: str,
        role_prompt: str | None,
        llm_specific_prompt: str | None,
        now_iso: str,
        chat_type: str,  # "direct" | "group"
        curated_stickers: Iterable[str] | None,
        history: Iterable[ChatMsg],
        target_message: ChatMsg | None,
        history_size: int = 500,
        include_speaker_prefix: bool = True,
        include_message_ids: bool = True,
        model: str | None = None,
        timeout_s: float | None = None,
    ) -> str:
        """
        Build contents using the parts-aware builder, extract a system instruction (if present),
        and call the Gemini model with *only* user/assistant turns. The system instruction is
        provided via the model config path (preferred) and never mixed into message contents.
        """
        contents = build_gemini_contents(
            persona_instructions=persona_instructions,
            role_prompt=role_prompt,
            llm_specific_prompt=llm_specific_prompt,
            now_iso=now_iso,
            chat_type=chat_type,
            curated_stickers=curated_stickers,
            history=history,
            target_message=target_message,
            history_size=history_size,
            include_speaker_prefix=include_speaker_prefix,
            include_message_ids=include_message_ids,
        )

        # Extract system instruction text if the builder produced a leading system turn.
        system_instruction: str | None = None
        contents_for_call = contents
        try:
            if contents and contents[0].get("role") == "system":
                parts = contents[0].get("parts") or []
                texts: list[str] = []
                for p in parts:
                    t = p.get("text")
                    if isinstance(t, str) and t:
                        texts.append(t)
                system_instruction = "\n\n".join(texts) if texts else None
                contents_for_call = contents[1:]
        except Exception:
            # If anything goes wrong, fall back to sending whatever we can (no system).
            system_instruction = None
            contents_for_call = contents

        # Optionally, lightweight structural logging
        if logger:
            try:
                total_turns = len(contents_for_call)
                hist_turns = max(
                    0, total_turns - (1 if target_message is not None else 0)
                )
                logger.debug(
                    "gemini.contents (no system in contents): turns=%s (history=%s, target=%s) has_sys=%s",
                    total_turns,
                    hist_turns,
                    target_message is not None,
                    bool(system_instruction),
                )
            except Exception:
                pass

        return await self._generate_with_contents(
            contents=contents_for_call,
            model=model,
            timeout_s=timeout_s,
            system_instruction=system_instruction,
        )
