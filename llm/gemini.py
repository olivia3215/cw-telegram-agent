# llm/gemini.py

from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterable

from .base import ChatMsg, build_gemini_contents

logger = logging.getLogger(__name__)


class GeminiLLM:
    """
    Provider-specific client using google-genai-style GenerativeModel.

    Expected attribute:
      - self.model: a GenerativeModel instance (already configured)
    """

    # --- internal: thin wrapper around the SDK ---

    async def _generate_with_contents(
        self,
        *,
        contents: list[dict],
        model: str | None = None,
        timeout_s: float | None = None,
        system_instruction: str | None = None,
    ) -> str:
        try:
            gm = getattr(self, "model", None)
            if gm is None:
                raise RuntimeError("Gemini model not initialized")

            # Normalize roles for Gemini: assistant -> model; allow only user/model.
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

            # Prefer passing system_instruction directly; fall back to a temp model if needed.
            if system_instruction:
                try:
                    response = await asyncio.to_thread(
                        gm.generate_content,
                        contents_norm,
                        system_instruction=system_instruction,
                    )
                except TypeError:
                    # Older SDK: construct a temporary GenerativeModel with system_instruction.
                    try:
                        model_name = getattr(gm, "model", None) or getattr(
                            self, "model_name", None
                        )
                        if not model_name:
                            raise RuntimeError("No Gemini model name available")
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
                        # Last resort: no system instruction; still do not coerce system into contents.
                        response = await asyncio.to_thread(
                            gm.generate_content, contents_norm
                        )
            else:
                response = await asyncio.to_thread(gm.generate_content, contents_norm)

            # Extract text defensively
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
                            first = content.parts[0]
                            if isinstance(first, dict) and "text" in first:
                                text = str(first["text"] or "")

            # Optional diagnostics hook (kept lightweight)
            try:
                cand_count = None
                if hasattr(response, "candidates") and response.candidates is not None:
                    try:
                        cand_count = len(response.candidates)
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

    # --- public API ---

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

        # Extract system text; never send a 'system' role in contents
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
