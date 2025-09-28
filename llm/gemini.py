# llm/gemini.py

import asyncio
import base64
import json
import logging
import os
from collections.abc import Iterable
from urllib import error, request

import google.generativeai as genai

from .base import LLM, ChatMsg
from .prompt_builder import build_gemini_contents

logger = logging.getLogger(__name__)


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

            # Optional comprehensive logging for debugging
            if os.getenv("GEMINI_DEBUG_LOGGING", "").lower() in ("true", "1", "yes"):
                logger.info("=== GEMINI DEBUG: COMPLETE PROMPT ===")
                logger.info(f"System Instruction: {system_instruction}")
                logger.info(f"Contents ({len(contents_norm)} turns):")
                for i, turn in enumerate(contents_norm):
                    role = turn.get("role", "unknown")
                    parts = turn.get("parts", [])
                    logger.info(f"  Turn {i+1} ({role}):")
                    for j, part in enumerate(parts):
                        if isinstance(part, dict) and "text" in part:
                            text = part["text"]
                            # Truncate very long text for readability
                            if len(text) > 1000:
                                text = text[:1000] + "... [truncated]"
                            logger.info(f"    Part {j+1}: {text}")
                        else:
                            logger.info(f"    Part {j+1}: {part}")
                logger.info("=== END GEMINI DEBUG: PROMPT ===")

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

            # Optional comprehensive logging for debugging
            if os.getenv("GEMINI_DEBUG_LOGGING", "").lower() in ("true", "1", "yes"):
                logger.info("=== GEMINI DEBUG: COMPLETE RESPONSE ===")
                logger.info(f"Response text: {text}")
                if response is not None:
                    logger.info(f"Response object type: {type(response)}")
                    if hasattr(response, "candidates") and response.candidates:
                        logger.info(f"Number of candidates: {len(response.candidates)}")
                        for i, candidate in enumerate(response.candidates):
                            logger.info(f"  Candidate {i+1}:")
                            if hasattr(candidate, "finish_reason"):
                                logger.info(
                                    f"    Finish reason: {candidate.finish_reason}"
                                )
                            if hasattr(candidate, "safety_ratings"):
                                logger.info(
                                    f"    Safety ratings: {candidate.safety_ratings}"
                                )
                logger.info("=== END GEMINI DEBUG: RESPONSE ===")

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
                # Target message is no longer appended as a separate turn
                hist_turns = total_turns
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
