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

    def __init__(self, model: str = "gemini-2.0-flash", api_key: str | None = None):
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

        # Prefer a vision-capable model; fall back to this instance's model if already 1.5.
        model = "gemini-1.5-pro"
        if (
            isinstance(getattr(self, "model_name", None), str)
            and "1.5" in self.model_name
        ):
            model = self.model_name

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
