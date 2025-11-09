# tests/test_retrieval_augmentation.py

# Copyright (c) 2025 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from handlers import received as hr
from handlers.received import _fetch_url, parse_llm_reply
from task_graph import TaskGraph


@pytest.mark.asyncio
async def test_parse_retrieve_task_single_url():
    """Test parsing a retrieve task with a single URL."""
    payload = json.dumps(
        [{"kind": "retrieve", "urls": ["https://example.com/page1"]}],
        indent=2,
    )
    tasks = await parse_llm_reply(payload, agent_id=123, channel_id=456)

    graph = TaskGraph(id="g", context={}, tasks=[])
    await hr._process_retrieve_tasks(
        tasks,
        agent=None,
        agent_name="TestAgent",
        channel_id=456,
        graph=graph,
        retrieved_urls={"https://example.com/page1"},
        retrieved_contents=[],
    )

    assert len(tasks) == 1
    assert tasks[0].type == "retrieve"
    assert tasks[0].params["urls"] == ["https://example.com/page1"]


@pytest.mark.asyncio
async def test_parse_retrieve_task_multiple_urls():
    """Test parsing a retrieve task with multiple URLs."""
    payload = json.dumps(
        [
            {
                "kind": "retrieve",
                "urls": [
                    "https://example.com/page1",
                    "https://example.com/page2",
                    "https://example.com/page3",
                ],
            }
        ],
        indent=2,
    )
    tasks = await parse_llm_reply(payload, agent_id=123, channel_id=456)

    graph = TaskGraph(id="g", context={}, tasks=[])
    tasks = await hr._process_retrieve_tasks(
        tasks,
        agent=None,
        agent_name="TestAgent",
        channel_id=456,
        graph=graph,
        retrieved_urls={
            "https://example.com/page1",
            "https://example.com/page2",
            "https://example.com/page3",
        },
        retrieved_contents=[],
    )

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
    payload = json.dumps(
        [
            {
                "kind": "retrieve",
                "text": "Here are some URLs:\nhttps://example.com/page1\nSome other text\nhttp://example.com/page2\n",
            }
        ],
        indent=2,
    )
    tasks = await parse_llm_reply(payload, agent_id=123, channel_id=456)

    graph = TaskGraph(id="g", context={}, tasks=[])
    tasks = await hr._process_retrieve_tasks(
        tasks,
        agent=None,
        agent_name="TestAgent",
        channel_id=456,
        graph=graph,
        retrieved_urls={"https://example.com/page1", "http://example.com/page2"},
        retrieved_contents=[],
    )

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
    payload = json.dumps(
        [{"kind": "retrieve", "text": "No URLs here!"}],
        indent=2,
    )
    tasks = await parse_llm_reply(payload, agent_id=123, channel_id=456)

    graph = TaskGraph(id="g", context={}, tasks=[])
    await hr._process_retrieve_tasks(
        tasks,
        agent=None,
        agent_name="TestAgent",
        channel_id=456,
        graph=graph,
        retrieved_urls=set(),
        retrieved_contents=[],
    )

    # Empty retrieve task should be discarded
    assert len([t for t in graph.tasks if t.type == "retrieve"]) == 0


@pytest.mark.asyncio
async def test_parse_mixed_tasks_with_retrieve():
    """Test parsing a mix of retrieve and other task types."""
    payload = json.dumps(
        [
            {"kind": "think", "text": "I should search for information first."},
            {
                "kind": "retrieve",
                "urls": ["https://www.google.com/search?q=test"],
            },
            {"kind": "send", "text": "Let me look that up for you!"},
        ],
        indent=2,
    )
    tasks = await parse_llm_reply(payload, agent_id=123, channel_id=456)

    graph = TaskGraph(id="g", context={}, tasks=[])
    await hr._process_retrieve_tasks(
        tasks,
        agent=None,
        agent_name="TestAgent",
        channel_id=456,
        graph=graph,
        retrieved_urls={"https://www.google.com/search?q=test"},
        retrieved_contents=[],
    )

    # Think task is discarded, so we should have retrieve and send
    assert len([t for t in tasks if t.type != "think"]) == 2
    retrieve_task = next(t for t in tasks if t.type == "retrieve")
    send_task = next(t for t in tasks if t.type == "send")
    assert retrieve_task.params["urls"] == ["https://www.google.com/search?q=test"]
    assert send_task.params["text"] == "Let me look that up for you!"
    assert "message" not in send_task.params
    assert "agent_id" not in send_task.params
    assert "channel_id" not in send_task.params


@pytest.mark.asyncio
async def test_fetch_url_success():
    """Test successful URL fetching with User-Agent header."""
    mock_response = MagicMock()
    mock_response.headers = {"content-type": "text/html; charset=utf-8"}
    mock_response.text = "<html><body>Test content</body></html>"

    mock_client = AsyncMock()
    mock_client.__aenter__.return_value.get = AsyncMock(return_value=mock_response)

    with patch("handlers.received.httpx.AsyncClient", return_value=mock_client):
        url, content = await _fetch_url("https://example.com")

        assert url == "https://example.com"
        assert content == "<html><body>Test content</body></html>"
        # Verify headers are set for no-JS compatibility
        call_args = mock_client.__aenter__.return_value.get.call_args
        assert call_args[0][0] == "https://example.com"
        headers = call_args[1]["headers"]
        assert "User-Agent" in headers
        assert "Mozilla" in headers["User-Agent"]
        assert "Accept" in headers
        assert "Accept-Language" in headers


@pytest.mark.asyncio
async def test_fetch_url_non_html():
    """Test fetching a non-HTML URL."""
    mock_response = MagicMock()
    mock_response.headers = {"content-type": "application/pdf"}

    mock_client = AsyncMock()
    mock_client.__aenter__.return_value.get = AsyncMock(return_value=mock_response)

    with patch("handlers.received.httpx.AsyncClient", return_value=mock_client):
        url, content = await _fetch_url("https://example.com/doc.pdf")

        assert url == "https://example.com/doc.pdf"
        assert "application/pdf" in content
        assert "not fetched" in content


@pytest.mark.asyncio
async def test_fetch_url_timeout():
    """Test URL fetching with timeout."""
    import httpx

    mock_client = AsyncMock()
    mock_client.__aenter__.return_value.get = AsyncMock(
        side_effect=httpx.TimeoutException("Timeout")
    )

    with patch("handlers.received.httpx.AsyncClient", return_value=mock_client):
        url, content = await _fetch_url("https://slow-site.com")

        assert url == "https://slow-site.com"
        assert "Request Timeout" in content
        assert "10 seconds" in content


@pytest.mark.asyncio
async def test_fetch_url_truncation():
    """Test that long content is truncated to 40k characters."""
    mock_response = MagicMock()
    mock_response.headers = {"content-type": "text/html"}
    # Create content longer than 40000 characters
    mock_response.text = "x" * 50000

    mock_client = AsyncMock()
    mock_client.__aenter__.return_value.get = AsyncMock(return_value=mock_response)

    with patch("handlers.received.httpx.AsyncClient", return_value=mock_client):
        url, content = await _fetch_url("https://example.com")

        assert url == "https://example.com"
        assert len(content) <= 40100  # 40000 + truncation message
        assert "Content truncated" in content


@pytest.mark.asyncio
async def test_fetch_url_connection_error():
    """Test URL fetching with connection error."""
    import httpx

    mock_client = AsyncMock()
    mock_client.__aenter__.return_value.get = AsyncMock(
        side_effect=httpx.ConnectError("Connection refused")
    )

    with patch("handlers.received.httpx.AsyncClient", return_value=mock_client):
        url, content = await _fetch_url("https://unreachable.com")

        assert url == "https://unreachable.com"
        assert "Error:" in content
        assert "ConnectError" in content
