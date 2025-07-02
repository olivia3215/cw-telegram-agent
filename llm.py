# llm.py

from abc import ABC, abstractmethod
import json
import logging
from typing import Protocol
from openai import AsyncOpenAI
import httpx

logger = logging.getLogger(__name__)


class LLM(ABC):
    @abstractmethod
    async def query(self, system_prompt: str, user_prompt: str) -> str:
        pass


class ChatGPT(LLM):
    def __init__(self, api_key: str, model: str = "gpt-4o", temperature: float = 0.7):
        self.client = AsyncOpenAI(api_key=api_key)
        self.model = model
        self.temperature = temperature
        self.history_size = 50

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
    def __init__(self):
        self.history_size = 20
        pass

    async def query(self, system: str, user: str) -> str:
        pass
