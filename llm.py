# llm.py

from abc import ABC, abstractmethod
import asyncio
import json
import logging
import os
from typing import Optional, Protocol
from openai import AsyncOpenAI
import httpx
import google.generativeai as genai

logger = logging.getLogger(__name__)


class LLM(ABC):
    prompt_name: str = "Default"

    @abstractmethod
    async def query(self, system_prompt: str, user_prompt: str) -> str:
        pass


class ChatGPT(LLM):
    prompt_name = "ChatGPT"

    def __init__(self, api_key: Optional[str] = None, model: str = "gpt-4.1-nano", temperature: float = 0.7):
        self.api_key = api_key or os.getenv("OPENAI_API_KEY")
        if not self.api_key:
            raise ValueError("Missing ChatGPT API key. Set OPENAI_API_KEY or pass it explicitly.")
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
                {"role": "user", "content": user}
            ],
            "stream": False
        }

        async with httpx.AsyncClient() as client:
            response = await client.post(
                url,
                json=payload,
                timeout=60)
            response.raise_for_status()
            data = response.json()
        return data.get("message", {}).get("content", "")


class GeminiLLM(LLM):
    prompt_name = "Gemini"

    def __init__(self, model: str = "gemini-2.0-flash", api_key: Optional[str] = None):
        self.model_name = model
        self.api_key = api_key or os.getenv("GOOGLE_GEMINI_API_KEY")
        if not self.api_key:
            raise ValueError("Missing Gemini API key. Set GOOGLE_GEMINI_API_KEY or pass it explicitly.")
        genai.configure(api_key=self.api_key)
        self.model = genai.GenerativeModel(model)
        self.history_size = 500

    async def query(self, system_prompt: str, user_prompt: str) -> str:
        full_prompt = f"{system_prompt}\n\n{user_prompt}"
        # logger.warning(f"=====> prompt: {full_prompt}")
        response = await asyncio.to_thread(self.model.generate_content, full_prompt)
        # logger.warning(f"=====> response: {response}")
        return response.text
