# llm/gemini.py

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Iterable

from .base import ChatMsg, build_llm_contents

logger = logging.getLogger(__name__)

# Import the modern Google client (package: google-genai)
try:
    import google.genai as genai  # type: ignore
except Exception:  # pragma: no cover
    genai = None


class GeminiLLM:
    """
    Provider-specific client using the modern google-genai Client API.

    Call path:
      client.models.generate_content(
          model=<model_name>,
          contents=[...],              # roles: "user" | "model"
          system_instruction="...",    # optional
          generation_config=...,       # optional
          safety_settings=...,         # optional
      )
    """

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model_name: str | None = None,
        safety_settings: object | None = None,
        generation_config: object | None = None,
        history_size: int = 500,
        prompt_name: str | None = None,
    ) -> None:
        """Initialize google-genai Client."""
        if genai is None:
            raise RuntimeError(
                "google-genai is not installed; pip install google-genai"
            )

        key = api_key or os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")
        if not key:
            raise RuntimeError(
                "Gemini API key not provided (GOOGLE_API_KEY/GEMINI_API_KEY)"
            )

        name = (
            model_name
            or os.getenv("GEMINI_MODEL")
            or os.getenv("GOOGLE_GENAI_MODEL")
            or os.getenv("GOOGLE_MODEL")
            or "gemini-2.5-flash"
        )

        self.model_name = name
        self.safety_settings = safety_settings
        self.generation_config = generation_config
        self.history_size = int(history_size)

        # Which system prompt to load (used by handlers.received.load_system_prompt)
        # Priority: explicit arg → env → default
        self.prompt_name = (
            prompt_name
            or os.getenv("LLM_PROMPT")
            or os.getenv("PROMPT_NAME")
            or "Gemini"
        )

        try:
            self.client = genai.Client(api_key=key)  # type: ignore[attr-defined]
        except Exception as e:  # pragma: no cover
            raise RuntimeError(f"Failed to initialize google-genai client: {e}") from e

    async def _generate_with_contents(
        self,
        *,
        contents: list[dict],
        model: str | None = None,
        timeout_s: float | None = None,  # unused currently; preserved for API parity
        system_instruction: str | None = None,
    ) -> str:
        """
        Send normalized contents to Gemini via client.models.generate_content.
        - Only roles "user" and "model" are sent (assistant → model).
        - system_instruction is passed via API kwarg when supported.
        Returns extracted text ('' if none).
        """
        try:
            client = getattr(self, "client", None)
            if client is None:
                raise RuntimeError("Gemini client not initialized")

            # Normalize roles for Gemini: assistant -> model; only "user"/"model".
            try:
                contents_norm: list[dict] = []
                for turn in contents:
                    role = turn.get("role")
                    if role == "assistant":
                        mapped_role = "model"
                    elif role == "user":
                        mapped_role = "user"
                    else:
                        mapped_role = "user"
                    parts = turn.get("parts") or []
                    contents_norm.append({"role": mapped_role, "parts": parts})
            except Exception:
                contents_norm = contents

            # Prepare kwargs; include optional configs when present.
            kwargs: dict = {
                "model": (model or self.model_name),
                "contents": contents_norm,
            }
            if system_instruction:
                kwargs["system_instruction"] = system_instruction
            if self.generation_config is not None:
                kwargs["generation_config"] = self.generation_config
            if self.safety_settings is not None:
                kwargs["safety_settings"] = self.safety_settings

            # Call the API; drop unsupported kwargs progressively on TypeError.
            try:
                response = await asyncio.to_thread(
                    client.models.generate_content, **kwargs
                )
            except TypeError:
                # Some versions may not accept system_instruction
                kwargs.pop("system_instruction", None)
                try:
                    response = await asyncio.to_thread(
                        client.models.generate_content, **kwargs
                    )
                except TypeError:
                    # Some versions may not accept safety/generation_config as kwargs
                    kwargs.pop("generation_config", None)
                    kwargs.pop("safety_settings", None)
                    response = await asyncio.to_thread(
                        client.models.generate_content, **kwargs
                    )

            # Extract text defensively across response shapes.
            text = ""
            if response is not None:
                # Modern responses frequently expose .text or .output_text
                if hasattr(response, "text") and isinstance(response.text, str):
                    text = response.text or ""
                elif hasattr(response, "output_text") and isinstance(
                    response.output_text, str
                ):
                    text = response.output_text or ""
                elif hasattr(response, "candidates") and response.candidates:
                    cand = response.candidates[0]
                    t = getattr(cand, "text", None)
                    if isinstance(t, str):
                        text = t or ""
                    else:
                        content = getattr(cand, "content", None)
                        parts = (
                            getattr(content, "parts", None)
                            if content is not None
                            else None
                        )
                        if parts:
                            first = parts[0]
                            if isinstance(first, dict) and "text" in first:
                                text = str(first["text"] or "")

            # Optional diagnostics
            try:
                cand_count = None
                if hasattr(response, "candidates") and response.candidates is not None:
                    try:
                        cand_count = len(response.candidates)  # type: ignore[arg-type]
                    except Exception:
                        cand_count = None
                if cand_count is None:
                    cand_count = (
                        1 if isinstance(getattr(response, "text", None), str) else 0
                    )
                finish_reason = None
                if hasattr(response, "candidates") and response.candidates:
                    first = response.candidates[0]
                    finish_reason = getattr(first, "finishReason", None) or getattr(
                        first, "finish_reason", None
                    )
                logger.debug(
                    "gemini.response: candidates=%s finish_reason=%s",
                    cand_count,
                    finish_reason,
                )
            except Exception:
                pass

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
        # Build full contents (includes a leading system turn).
        contents = build_llm_contents(
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

        # Extract system text; do NOT send role="system" to Gemini.
        system_instruction: str | None = None
        contents_no_system = contents
        if contents and contents[0].get("role") == "system":
            parts = contents[0].get("parts") or []
            texts: list[str] = []
            for p in parts:
                t = p.get("text")
                if isinstance(t, str) and t:
                    texts.append(t)
            system_instruction = "\n\n".join(texts) if texts else None
            contents_no_system = contents[1:]

        # Optional structure log
        try:
            total_turns = len(contents_no_system)
            hist_turns = max(0, total_turns - (1 if target_message is not None else 0))
            logger.debug(
                "gemini.contents: turns=%s (history=%s, target=%s) has_sys=%s",
                total_turns,
                hist_turns,
                target_message is not None,
                bool(system_instruction),
            )
        except Exception:
            pass

        return await self._generate_with_contents(
            contents=contents_no_system,
            model=model,
            timeout_s=timeout_s,
            system_instruction=system_instruction,
        )

    # --- LLM image capability (provider-specific; safe stubs for now) ---

    def is_supported_image(
        self, *, mime_type: str | None = None, media_kind: str | None = None
    ) -> bool:
        if not mime_type:
            return False
        mt = mime_type.lower()
        # Conservative allow-list; broaden as we verify end-to-end behavior.
        return mt in {
            "image/jpeg",
            "image/jpg",
            "image/png",
            "image/webp",
            "image/gif",
            "image/heic",
            "image/heif",
        }

    async def describe_image(
        self,
        *,
        image_bytes: bytes,
        mime_type: str | None = None,
        prompt: str | None = None,
    ) -> str:
        """
        Stub: we currently use compact rendered descriptions for media. To keep
        tests fast/offline, we do not invoke a vision model here. Return empty
        string so callers can fall back to cached renderings/placeholders.
        """
        return ""
