# llm.py

from abc import ABC, abstractmethod
import logging
from typing import Protocol
from openai import AsyncOpenAI

logger = logging.getLogger(__name__)


class LLM(ABC):
    @abstractmethod
    async def query(self, system_prompt: str, user_prompt: str) -> str:
        pass


class ChatGPT(LLM):
    def __init__(self, api_key: str, model: str = "gpt-4", temperature: float = 0.7):
        self.client = AsyncOpenAI(api_key=api_key)
        self.model = model
        self.temperature = temperature

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