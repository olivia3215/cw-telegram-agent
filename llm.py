# llm.py

import asyncio
import base64
import json
import logging
import os
from abc import ABC, abstractmethod
from urllib import error, request

import google.generativeai as genai
import httpx
from openai import AsyncOpenAI

logger = logging.getLogger(__name__)
# --- Gemini config (model + safety) -----------------------------------------

# Use the newer preview model
# GEMINI_MODEL_DEFAULT = "gemini-2.5-flash-preview-09-2025"
GEMINI_MODEL_DEFAULT = "gemini-2.0-flash"

# Hard-coded safety settings: disable category blocking (BLOCK_NONE)
# API expects these exact strings in REST payloads.
GEMINI_SAFETY_SETTINGS = [
    {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
    {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
    {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
    {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
]


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


def _gemini_payload_with_safety(
    contents: list, generation_config: dict | None = None
) -> dict:
    """
    Build a Gemini REST payload that always includes our BLOCK_NONE safety settings.
    `contents` is the usual Gemini "contents" array. `generation_config` is optional.
    """
    payload = {
        "contents": contents,
        "safetySettings": GEMINI_SAFETY_SETTINGS,
    }
    if generation_config:
        payload["generationConfig"] = generation_config
    return payload


def _log_safety_findings(resp_json, *, context: str):
    """Log a concise warning if Gemini returns safety blocks/findings."""
    try:
        safety = []
        # candidates[].safetyRatings[] or promptFeedback.safetyRatings[]
        for c in resp_json.get("candidates") or []:
            for r in c.get("safetyRatings") or []:
                if r.get("blocked", False) or (
                    r.get("probability") in ("HIGH", "MEDIUM")
                ):
                    safety.append(f"{r.get('category')}:{r.get('probability')}")
        pf = resp_json.get("promptFeedback") or {}
        for r in pf.get("safetyRatings") or []:
            if r.get("blocked", False) or (r.get("probability") in ("HIGH", "MEDIUM")):
                safety.append(f"{r.get('category')}:{r.get('probability')}")
        if safety:
            logger.warning(f"[gemini][{context}] safety: " + ", ".join(safety))
    except Exception:
        pass


def _sdk_safety_settings():
    # Prefer typed settings if available; otherwise fall back to dicts.
    try:
        # google.generativeai.types on older SDKs
        from google.generativeai.types import (
            HarmBlockThreshold,
            HarmCategory,
            SafetySetting,
        )

        return [
            SafetySetting(
                category=HarmCategory.HARM_CATEGORY_HARASSMENT,
                threshold=HarmBlockThreshold.BLOCK_NONE,
            ),
            SafetySetting(
                category=HarmCategory.HARM_CATEGORY_HATE_SPEECH,
                threshold=HarmBlockThreshold.BLOCK_NONE,
            ),
            SafetySetting(
                category=HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT,
                threshold=HarmBlockThreshold.BLOCK_NONE,
            ),
            SafetySetting(
                category=HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT,
                threshold=HarmBlockThreshold.BLOCK_NONE,
            ),
        ]
    except Exception:
        return GEMINI_SAFETY_SETTINGS


class GeminiLLM(LLM):
    prompt_name = "Gemini"

    def __init__(
        self, model_name: str = GEMINI_MODEL_DEFAULT, api_key: str | None = None
    ):
        self.model_name = model_name
        self.api_key = api_key or os.getenv("GOOGLE_GEMINI_API_KEY")
        if not self.api_key:
            raise ValueError(
                "Missing Gemini API key. Set GOOGLE_GEMINI_API_KEY or pass it explicitly."
            )
        genai.configure(api_key=self.api_key)
        self.model = genai.GenerativeModel(self.model_name)
        self.history_size = 500

    async def query(self, system_prompt: str, user_prompt: str) -> str:
        full_prompt = f"{system_prompt}\n\n{user_prompt}"
        logger.debug(
            f"[gemini] prompt chars={len(full_prompt)} model={self.model_name}"
        )

        # Optional: tune generation settings if you like
        generation_config = {
            "temperature": 0.4,
            "top_p": 0.95,
            "top_k": 40,
            "max_output_tokens": 1024,
        }

        # Call SDK with safety settings; run off-loop
        def _call():
            return self.model.generate_content(
                full_prompt,
                safety_settings=(
                    _sdk_safety_settings()
                    if " _sdk_safety_settings" in globals()
                    else GEMINI_SAFETY_SETTINGS
                ),
                generation_config=generation_config,
            )

        response = await asyncio.to_thread(_call)

        # Log safety findings if present (convert SDK object to dict if possible)
        try:
            as_dict = response.to_dict() if hasattr(response, "to_dict") else {}
            if as_dict:
                _log_safety_findings(as_dict, context="query")
        except Exception:
            pass

        # Return the main text result
        try:
            return response.text
        except Exception:
            # Robust fallback in case .text is missing
            try:
                # candidates[0].content.parts[0].text style
                as_dict = response.to_dict()
                candidates = as_dict.get("candidates") or []
                parts = (candidates[0].get("content") or {}).get("parts") or []
                return (parts[0].get("text") or "").strip()
            except Exception as e:
                logger.warning(f"[gemini][query] no text in response: {e}")
                return ""

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

        # Use the same model we configured at init
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model_name}:generateContent?key={self.api_key}"

        contents = [
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
        payload = _gemini_payload_with_safety(contents)

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

            _log_safety_findings(obj, context="describe_image")

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
