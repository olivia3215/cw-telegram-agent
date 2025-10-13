# tests/test_retrieval_augmentation.py

# Copyright (c) 2025 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from handlers.received import _fetch_url, parse_llm_reply_from_markdown


@pytest.mark.asyncio
async def test_parse_retrieve_task_single_url():
    """Test parsing a retrieve task with a single URL."""
    markdown = """# «retrieve»

https://example.com/page1
"""
    tasks = await parse_llm_reply_from_markdown(markdown, agent_id=123, channel_id=456)

    assert len(tasks) == 1
    assert tasks[0].type == "retrieve"
    assert tasks[0].params["urls"] == ["https://example.com/page1"]


@pytest.mark.asyncio
async def test_parse_retrieve_task_multiple_urls():
    """Test parsing a retrieve task with multiple URLs."""
    markdown = """# «retrieve»

https://example.com/page1
https://example.com/page2
https://example.com/page3
"""
    tasks = await parse_llm_reply_from_markdown(markdown, agent_id=123, channel_id=456)

    assert len(tasks) == 1
    assert tasks[0].type == "retrieve"
    assert tasks[0].params["urls"] == [
        "https://example.com/page1",
        "https://example.com/page2",
        "https://example.com/page3",
    ]


@pytest.mark.asyncio
async def test_parse_retrieve_task_with_text():
    """Test parsing a retrieve task that includes non-URL text (should be ignored)."""
    markdown = """# «retrieve»

Here are some URLs:
https://example.com/page1
Some other text
http://example.com/page2
"""
    tasks = await parse_llm_reply_from_markdown(markdown, agent_id=123, channel_id=456)

    assert len(tasks) == 1
    assert tasks[0].type == "retrieve"
    # Only the actual URLs should be extracted
    assert tasks[0].params["urls"] == [
        "https://example.com/page1",
        "http://example.com/page2",
    ]


@pytest.mark.asyncio
async def test_parse_retrieve_task_empty():
    """Test that an empty retrieve task is not added to task list."""
    markdown = """# «retrieve»

No URLs here!
"""
    tasks = await parse_llm_reply_from_markdown(markdown, agent_id=123, channel_id=456)

    # Empty retrieve task should be discarded
    assert len(tasks) == 0


@pytest.mark.asyncio
async def test_parse_mixed_tasks_with_retrieve():
    """Test parsing a mix of retrieve and other task types."""
    markdown = """# «think»

I should search for information first.

# «retrieve»

https://www.google.com/search?q=test

# «send»

Let me look that up for you!
"""
    tasks = await parse_llm_reply_from_markdown(markdown, agent_id=123, channel_id=456)

    # Think task is discarded, so we should have retrieve and send
    assert len(tasks) == 2
    assert tasks[0].type == "retrieve"
    assert tasks[0].params["urls"] == ["https://www.google.com/search?q=test"]
    assert tasks[1].type == "send"
    assert tasks[1].params["message"] == "Let me look that up for you!"


@pytest.mark.asyncio
async def test_fetch_url_success():
    """Test successful URL fetching."""
    mock_response = MagicMock()
    mock_response.headers = {"content-type": "text/html; charset=utf-8"}
    mock_response.text = "<html><body>Test content</body></html>"

    with patch(
        "handlers.received.requests.get", return_value=mock_response
    ) as mock_get:
        url, content = await _fetch_url("https://example.com")

        assert url == "https://example.com"
        assert content == "<html><body>Test content</body></html>"
        mock_get.assert_called_once_with(
            "https://example.com", timeout=10, allow_redirects=True
        )


@pytest.mark.asyncio
async def test_fetch_url_non_html():
    """Test fetching a non-HTML URL."""
    mock_response = MagicMock()
    mock_response.headers = {"content-type": "application/pdf"}

    with patch("handlers.received.requests.get", return_value=mock_response):
        url, content = await _fetch_url("https://example.com/doc.pdf")

        assert url == "https://example.com/doc.pdf"
        assert "application/pdf" in content
        assert "not fetched" in content


@pytest.mark.asyncio
async def test_fetch_url_timeout():
    """Test URL fetching with timeout."""
    import requests

    with patch(
        "handlers.received.requests.get", side_effect=requests.exceptions.Timeout
    ):
        url, content = await _fetch_url("https://slow-site.com")

        assert url == "https://slow-site.com"
        assert "Request Timeout" in content
        assert "10 seconds" in content


@pytest.mark.asyncio
async def test_fetch_url_truncation():
    """Test that long content is truncated to 8k characters."""
    mock_response = MagicMock()
    mock_response.headers = {"content-type": "text/html"}
    # Create content longer than 8000 characters
    mock_response.text = "x" * 10000

    with patch("handlers.received.requests.get", return_value=mock_response):
        url, content = await _fetch_url("https://example.com")

        assert url == "https://example.com"
        assert len(content) <= 8100  # 8000 + truncation message
        assert "Content truncated" in content


@pytest.mark.asyncio
async def test_fetch_url_connection_error():
    """Test URL fetching with connection error."""
    import requests

    with patch(
        "handlers.received.requests.get",
        side_effect=requests.exceptions.ConnectionError("Connection refused"),
    ):
        url, content = await _fetch_url("https://unreachable.com")

        assert url == "https://unreachable.com"
        assert "Error:" in content
        assert "ConnectionError" in content
