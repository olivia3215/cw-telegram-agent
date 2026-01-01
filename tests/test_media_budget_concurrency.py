import asyncio
from types import SimpleNamespace
from unittest.mock import patch, AsyncMock, MagicMock
import pytest
from media.media_source import (
    AIChainMediaSource,
    AIGeneratingMediaSource,
    BudgetExhaustedMediaSource,
    MediaStatus,
    NothingMediaSource,
    CompositeMediaSource,
)
from media.media_budget import reset_description_budget, get_remaining_description_budget

class FakeLLM:
    def __init__(self):
        self.call_count = 0

    async def describe_image(self, *args, **kwargs):
        self.call_count += 1
        # Add a small delay to allow concurrency
        await asyncio.sleep(0.1)
        return "fake description"

    def is_mime_type_supported_by_llm(self, mime_type: str) -> bool:
        return True

@pytest.mark.asyncio
async def test_concurrent_budget_consumption(tmp_path):
    """
    With a budget of 1, if multiple requests are processed concurrently,
    only one should succeed and the rest should get BUDGET_EXHAUSTED.
    """
    llm = FakeLLM()
    agent = SimpleNamespace(client=MagicMock(), llm=llm)
    
    # Mock download_media_bytes to add a delay
    async def fake_download(*args, **kwargs):
        await asyncio.sleep(0.1)
        return b"fake_data"

    ai_cache_dir = tmp_path / "media"
    ai_cache_dir.mkdir()

    media_chain = CompositeMediaSource([
        BudgetExhaustedMediaSource(),
        AIGeneratingMediaSource(cache_directory=ai_cache_dir),
    ])

    # Budget = 1
    reset_description_budget(1)

    with patch("media.sources.ai_generating.download_media_bytes", side_effect=fake_download), \
         patch("media.sources.ai_generating.get_media_llm", return_value=llm):
        
        # Run 3 requests concurrently
        tasks = [
            media_chain.get(unique_id=f"uid-{i}", agent=agent, doc=SimpleNamespace(uid=f"uid-{i}"), kind="photo")
            for i in range(3)
        ]
        results = await asyncio.gather(*tasks)

    # Assert: Exactly one should have status GENERATED
    generated = [r for r in results if r["status"] == MediaStatus.GENERATED.value]
    exhausted = [r for r in results if r["status"] == MediaStatus.BUDGET_EXHAUSTED.value]
    
    assert len(generated) == 1
    assert len(exhausted) == 2
    assert llm.call_count == 1
    assert get_remaining_description_budget() == 0

