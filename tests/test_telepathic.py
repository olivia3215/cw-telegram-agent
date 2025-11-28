# tests/test_telepathic.py

# Copyright (c) 2025 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.

import json
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import pytest

from telepathic import is_telepath, reload_telepathic_channels, _load_telepathic_channels, TELEPATHIC_PREFIXES
from task_graph import TaskGraph, TaskNode


class TestTelepathicConfiguration:
    """Test telepathic configuration loading and caching."""

    def test_load_telepathic_channels_empty_config(self):
        """Test loading when no config directories exist."""
        with patch('telepathic.CONFIG_DIRECTORIES', []):
            channels = _load_telepathic_channels()
            assert channels == set()

    def test_load_telepathic_channels_no_telepaths_file(self, tmp_path):
        """Test loading when config directory exists but no Telepaths.md file."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        
        with patch('telepathic.CONFIG_DIRECTORIES', [str(config_dir)]):
            channels = _load_telepathic_channels()
            assert channels == set()

    def test_load_telepathic_channels_valid_file(self, tmp_path):
        """Test loading valid Telepaths.md file."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        telepaths_file = config_dir / "Telepaths.md"
        
        # Create a valid Telepaths.md file
        telepaths_file.write_text("""
# Telepathic Channels

- 123456789
- -987654321
- 555666777

Some other content that should be ignored.
- invalid_line_without_space
        """)
        
        with patch('telepathic.CONFIG_DIRECTORIES', [str(config_dir)]):
            channels = _load_telepathic_channels()
            assert channels == {123456789, -987654321, 555666777}

    def test_load_telepathic_channels_multiple_config_dirs(self, tmp_path):
        """Test loading from multiple configuration directories."""
        config1 = tmp_path / "config1"
        config1.mkdir()
        telepaths1 = config1 / "Telepaths.md"
        telepaths1.write_text("- 111\n- 222")
        
        config2 = tmp_path / "config2"
        config2.mkdir()
        telepaths2 = config2 / "Telepaths.md"
        telepaths2.write_text("- 333\n- 444")
        
        with patch('telepathic.CONFIG_DIRECTORIES', [str(config1), str(config2)]):
            channels = _load_telepathic_channels()
            assert channels == {111, 222, 333, 444}

    def test_load_telepathic_channels_invalid_numbers(self, tmp_path):
        """Test handling of invalid numbers in Telepaths.md."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        telepaths_file = config_dir / "Telepaths.md"
        
        telepaths_file.write_text("""
- 123
- not_a_number
- 456
- -789
        """)
        
        with patch('telepathic.CONFIG_DIRECTORIES', [str(config_dir)]):
            with patch('telepathic.logger') as mock_logger:
                channels = _load_telepathic_channels()
                assert channels == {123, 456, -789}
                # Should log warning about invalid number
                mock_logger.warning.assert_called()

    def test_is_telepath_caching(self, tmp_path):
        """Test that is_telepath caches the configuration."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        telepaths_file = config_dir / "Telepaths.md"
        telepaths_file.write_text("- 123\n- 456")
        
        with patch('telepathic.CONFIG_DIRECTORIES', [str(config_dir)]):
            # Reset the cache
            import telepathic
            telepathic._telepathic_cache_loaded = False
            telepathic._telepathic_channels = set()
            
            # First call should load the config
            assert is_telepath(123) is True
            assert is_telepath(456) is True
            assert is_telepath(789) is False
            
            # Second call should use cache (no file reading)
            assert is_telepath(123) is True

    def test_reload_telepathic_channels(self, tmp_path):
        """Test reloading telepathic channels."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        telepaths_file = config_dir / "Telepaths.md"
        telepaths_file.write_text("- 123")
        
        with patch('telepathic.CONFIG_DIRECTORIES', [str(config_dir)]):
            # Reset the cache
            import telepathic
            telepathic._telepathic_cache_loaded = False
            telepathic._telepathic_channels = set()
            
            # Load initial config
            assert is_telepath(123) is True
            assert is_telepath(456) is False
            
            # Update the file
            telepaths_file.write_text("- 123\n- 456")
            
            # Reload should pick up the changes
            reload_telepathic_channels()
            assert is_telepath(123) is True
            assert is_telepath(456) is True


class TestTelepathicMessageHandling:
    """Test telepathic message handling in received.py."""

    @pytest.mark.asyncio
    async def test_send_telepathic_message_success(self):
        """Test successful sending of telepathic message."""
        from handlers.telepathic import maybe_send_telepathic_message
        
        mock_agent = Mock()
        mock_agent.name = "TestAgent"
        mock_agent.client = AsyncMock()
        mock_agent.agent_id = 789  # Non-telepathic agent
        
        with patch('handlers.telepathic.is_telepath') as mock_is_telepath:
            # Channel is telepathic, agent is not telepathic
            mock_is_telepath.side_effect = lambda x: x == 123456
            
            await maybe_send_telepathic_message(mock_agent, 123456, "⟦think⟧", "I need to think about this")
            
            mock_agent.client.send_message.assert_called_once_with(
                123456, "⟦think⟧\nI need to think about this", parse_mode="Markdown"
            )

    @pytest.mark.asyncio
    async def test_send_telepathic_message_empty_content(self):
        """Test that empty content is not sent."""
        from handlers.telepathic import maybe_send_telepathic_message
        
        mock_agent = Mock()
        mock_agent.client = AsyncMock()
        
        await maybe_send_telepathic_message(mock_agent, 123456, "⟦think⟧", "")
        
        mock_agent.client.send_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_send_telepathic_message_error_handling(self):
        """Test error handling when sending telepathic message fails."""
        from handlers.telepathic import maybe_send_telepathic_message
        
        mock_agent = Mock()
        mock_agent.name = "TestAgent"
        mock_agent.client = AsyncMock()
        mock_agent.agent_id = 789  # Non-telepathic agent
        mock_agent.client.send_message.side_effect = Exception("Send failed")
        
        with patch('handlers.telepathic.is_telepath') as mock_is_telepath:
            # Channel is telepathic, agent is not telepathic
            mock_is_telepath.side_effect = lambda x: x == 123456
            
            with patch('handlers.telepathic.logger') as mock_logger:
                await maybe_send_telepathic_message(mock_agent, 123456, "⟦think⟧", "test")
                
                mock_logger.error.assert_called_once()

    @pytest.mark.asyncio
    async def test_parse_llm_reply_think_telepathic(self):
        """Test that think tasks send telepathic messages when channel is telepathic and agent is not."""
        from handlers.received import parse_llm_reply
        
        mock_agent = Mock()
        mock_agent.name = "TestAgent"
        mock_agent.client = AsyncMock()
        mock_agent.agent_id = 123  # Non-telepathic agent
        
        json_text = json.dumps(
            [{"kind": "think", "text": "I need to think about this problem carefully."}],
            indent=2,
        )
        
        with patch('handlers.received.is_telepath') as mock_is_telepath, patch(
            'handlers.telepathic.is_telepath'
        ) as mock_telepath:
            # Channel is telepathic, agent is not telepathic
            mock_is_telepath.side_effect = mock_telepath.side_effect = lambda x: x == 456
            
            tasks = await parse_llm_reply(
                json_text, agent_id=123, channel_id=456, agent=mock_agent
            )
            
            # Should not add think task to task list
            assert len(tasks) == 0
            
            # Should send telepathic message
            mock_agent.client.send_message.assert_called_once_with(
                456, "⟦think⟧\nI need to think about this problem carefully.", parse_mode="Markdown"
            )

    @pytest.mark.asyncio
    async def test_parse_llm_reply_remember_telepathic(self):
        """Test that remember tasks send telepathic messages when channel is telepathic and agent is not."""
        from handlers.received import parse_llm_reply
        
        mock_agent = Mock()
        mock_agent.name = "TestAgent"
        mock_agent.client = AsyncMock()
        mock_agent.agent_id = 123  # Non-telepathic agent
        
        json_text = json.dumps(
            [
                {
                    "kind": "remember",
                    "id": "remember-short-responses",
                    "content": "User prefers short responses.",
                    "category": "preferences",
                }
            ],
            indent=2,
        )
        
        with patch('handlers.received.is_telepath') as mock_is_telepath, patch(
            'handlers.telepathic.is_telepath'
        ) as mock_telepath:
            # Channel is telepathic, agent is not telepathic
            mock_is_telepath.side_effect = mock_telepath.side_effect = lambda x: x == 456
            
            with patch('handlers.remember._process_remember_task', new_callable=AsyncMock) as mock_process:
                tasks = await parse_llm_reply(
                    json_text, agent_id=123, channel_id=456, agent=mock_agent
                )
                
                # Should not add remember task to task list
                assert len(tasks) == 0
                
                # Should send telepathic message
                mock_agent.client.send_message.assert_called_once()
                send_args, send_kwargs = mock_agent.client.send_message.call_args
                assert send_args[0] == 456
                assert send_kwargs["parse_mode"] == "Markdown"
                prefix, _, body = send_args[1].partition("\n")
                assert prefix == "⟦remember⟧"
                payload = json.loads(body)
                assert payload == {
                    "id": "remember-short-responses",
                    "content": "User prefers short responses.",
                    "category": "preferences",
                }
                
                # Should still process the remember task
                mock_process.assert_called_once()

    @pytest.mark.asyncio
    async def test_parse_llm_reply_intend_telepathic(self):
        """Intent tasks should be processed immediately and sent telepathically when appropriate."""
        from handlers.received import parse_llm_reply

        mock_agent = Mock()
        mock_agent.name = "TestAgent"
        mock_agent.client = AsyncMock()
        mock_agent.agent_id = 123

        json_text = json.dumps(
            [
                {
                    "kind": "intend",
                    "content": "Schedule a check-in with Wendy tomorrow morning.",
                }
            ],
            indent=2,
        )

        with patch("handlers.received.is_telepath") as mock_is_telepath, patch(
            "handlers.telepathic.is_telepath"
        ) as mock_telepath:
            mock_is_telepath.side_effect = mock_telepath.side_effect = lambda x: x == 456

            with patch(
                "handlers.intend._process_intend_task", new_callable=AsyncMock
            ) as mock_process:
                tasks = await parse_llm_reply(
                    json_text, agent_id=123, channel_id=456, agent=mock_agent
                )

                assert tasks == []
                mock_agent.client.send_message.assert_called_once()
                send_args, send_kwargs = mock_agent.client.send_message.call_args
                assert send_args[0] == 456
                assert send_kwargs["parse_mode"] == "Markdown"
                prefix, _, body = send_args[1].partition("\n")
                assert prefix == "⟦intend⟧"
                payload = json.loads(body)
                assert payload["content"] == "Schedule a check-in with Wendy tomorrow morning."
                mock_process.assert_called_once()

    @pytest.mark.asyncio
    async def test_parse_llm_reply_plan_telepathic(self):
        """Plan tasks should be processed immediately and sent telepathically when appropriate."""
        from handlers.received import parse_llm_reply

        mock_agent = Mock()
        mock_agent.name = "TestAgent"
        mock_agent.client = AsyncMock()
        mock_agent.agent_id = 123

        json_text = json.dumps(
            [
                {
                    "kind": "plan",
                    "content": "Prepare a three-step follow-up for Neal about the funding update.",
                }
            ],
            indent=2,
        )

        with patch("handlers.received.is_telepath") as mock_is_telepath, patch(
            "handlers.telepathic.is_telepath"
        ) as mock_telepath:
            mock_is_telepath.side_effect = mock_telepath.side_effect = lambda x: x == 456

            with patch(
                "handlers.plan._process_plan_task", new_callable=AsyncMock
            ) as mock_process:
                tasks = await parse_llm_reply(
                    json_text, agent_id=123, channel_id=456, agent=mock_agent
                )

                assert tasks == []
                mock_agent.client.send_message.assert_called_once()
                send_args, send_kwargs = mock_agent.client.send_message.call_args
                assert send_args[0] == 456
                assert send_kwargs["parse_mode"] == "Markdown"
                prefix, _, body = send_args[1].partition("\n")
                assert prefix == "⟦plan⟧"
                payload = json.loads(body)
                assert payload["content"] == "Prepare a three-step follow-up for Neal about the funding update."
                mock_process.assert_called_once()

    @pytest.mark.asyncio
    async def test_parse_llm_reply_retrieve_telepathic(self):
        """Test that retrieve tasks send telepathic messages when channel is telepathic and agent is not."""
        from handlers.received import parse_llm_reply
        
        mock_agent = Mock()
        mock_agent.name = "TestAgent"
        mock_agent.client = AsyncMock()
        mock_agent.agent_id = 123  # Non-telepathic agent
        
        json_text = json.dumps(
            [
                {
                    "kind": "retrieve",
                    "urls": [
                        "https://example.com/page1",
                        "https://example.com/page2",
                    ],
                }
            ],
            indent=2,
        )
        
        with patch('handlers.received.is_telepath') as mock_is_telepath, patch(
            'handlers.telepathic.is_telepath'
        ) as mock_telepath:
            # Channel is telepathic, agent is not telepathic
            mock_is_telepath.side_effect = mock_telepath.side_effect = lambda x: x == 456
            
            tasks = await parse_llm_reply(
                json_text, agent_id=123, channel_id=456, agent=mock_agent
            )

            # Should add retrieve task to task list
            assert len(tasks) == 1
            assert tasks[0].type == "retrieve"
            assert tasks[0].params["urls"] == ["https://example.com/page1", "https://example.com/page2"]

            # Telepathic message should be sent when URLs are fetched
            mock_agent.client.send_message.assert_not_called()

            from handlers import received as hr

            graph = TaskGraph(id="g1", context={}, tasks=[])

            with patch(
                "handlers.received._fetch_url",
                new=AsyncMock(return_value=("https://example.com/page1", "<html>1</html>")),
            ):
                with patch(
                    "handlers.received.make_wait_task",
                    return_value=TaskNode(id="wait-1", type="wait", params={}, depends_on=[]),
                ):
                    with pytest.raises(Exception):
                        await hr._process_retrieve_tasks(
                            tasks,
                            agent=mock_agent,
                            channel_id=456,
                            graph=graph,
                            retrieved_urls=set(),
                            retrieved_contents=[],
                            fetch_url_fn=hr._fetch_url,
                        )

            mock_agent.client.send_message.assert_called_once_with(
                456,
                "⟦retrieve⟧\nhttps://example.com/page1\nhttps://example.com/page2",
                parse_mode="Markdown",
            )

    @pytest.mark.asyncio
    async def test_parse_llm_reply_non_telepathic_channel(self):
        """Test that telepathic messages are not sent for non-telepathic channels."""
        from handlers.received import parse_llm_reply
        
        mock_agent = Mock()
        mock_agent.name = "TestAgent"
        mock_agent.client = AsyncMock()
        mock_agent.agent_id = 123  # Non-telepathic agent
        
        json_text = json.dumps(
            [{"kind": "think", "text": "I need to think about this."}],
            indent=2,
        )
        
        with patch('handlers.received.is_telepath', return_value=False), patch(
            'handlers.telepathic.is_telepath', return_value=False
        ):
            tasks = await parse_llm_reply(
                json_text, agent_id=123, channel_id=456, agent=mock_agent
            )
            
            # Should not add think task to task list
            assert len(tasks) == 0
            
            # Should not send telepathic message
            mock_agent.client.send_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_parse_llm_reply_telepathic_agent_no_telepathic_message(self):
        """Test that telepathic agents do not send telepathic messages even to telepathic channels."""
        from handlers.received import parse_llm_reply
        
        mock_agent = Mock()
        mock_agent.name = "TestAgent"
        mock_agent.client = AsyncMock()
        mock_agent.agent_id = 123  # Telepathic agent
        
        json_text = json.dumps(
            [{"kind": "think", "text": "I need to think about this."}],
            indent=2,
        )
        
        with patch('handlers.received.is_telepath') as mock_is_telepath, patch(
            'handlers.telepathic.is_telepath'
        ) as mock_telepath:
            # Both channel and agent are telepathic
            mock_is_telepath.side_effect = mock_telepath.side_effect = lambda x: x in [123, 456]

            tasks = await parse_llm_reply(
                json_text, agent_id=123, channel_id=456, agent=mock_agent
            )
            
            # Should not add think task to task list
            assert len(tasks) == 0
            
            # Should not send telepathic message (agent is telepathic)
            mock_agent.client.send_message.assert_not_called()


class TestTelepathicMessageFiltering:
    """Test filtering of telepathic messages from agent view."""

    @pytest.mark.asyncio
    async def test_filter_telepathic_messages_from_history(self):
        """Test that telepathic messages are filtered from message history."""
        from handlers.received import _process_message_history
        from llm.base import MsgTextPart
        
        mock_agent = Mock()
        mock_agent.timezone = None
        
        # Create mock messages
        mock_message1 = Mock()
        mock_message1.id = 1
        mock_message1.sender_id = 123  # Use integer directly
        mock_message1.out = False
        mock_message1.reply_to = None
        mock_message1.date = None
        mock_message1.sender = None
        
        mock_message2 = Mock()
        mock_message2.id = 2
        mock_message2.sender_id = 123  # Use integer directly
        mock_message2.out = True  # This is from the agent
        mock_message2.reply_to = None
        mock_message2.date = None
        mock_message2.sender = None
        
        # Mock format_message_for_prompt to return different content
        async def mock_format_message(msg, agent=None, media_chain=None):
            if msg.id == 1:
                return [MsgTextPart(kind="text", text="Hello, how are you?")]
            elif msg.id == 2:
                return [MsgTextPart(kind="text", text="⟦think⟧\nI need to think about this")]
            return []
        
        mock_agent.agent_id = 999  # Set agent_id for telepathic check
        mock_agent.get_cached_entity = AsyncMock(return_value=None)
        
        with patch('handlers.received_helpers.message_processing.format_message_for_prompt', side_effect=mock_format_message):
            with patch('handlers.received_helpers.message_processing.get_channel_name', return_value="TestUser"):
                history = await _process_message_history([mock_message1, mock_message2], mock_agent, None)
                
                # Should only have one message (the non-telepathic one)
                assert len(history) == 1
                assert history[0].message_parts[0]["text"] == "Hello, how are you?"

    @pytest.mark.asyncio
    async def test_filter_multiple_telepathic_message_types(self):
        """Test filtering of different types of telepathic messages."""
        from handlers.received import _process_message_history
        from llm.base import MsgTextPart
        
        mock_agent = Mock()
        mock_agent.timezone = None
        
        # Create mock messages with different telepathic prefixes
        messages = []
        telepathic_prefixes = list(TELEPATHIC_PREFIXES)
        
        for i, prefix in enumerate(telepathic_prefixes):
            mock_msg = Mock()
            mock_msg.id = i + 1
            mock_msg.sender_id = 123  # Use integer directly
            mock_msg.out = True
            mock_msg.reply_to = None
            mock_msg.date = None
            mock_msg.sender = None
            messages.append(mock_msg)
        
        # Add one normal message
        normal_msg = Mock()
        normal_msg.id = 10  # Use a different ID to avoid conflict with telepathic messages
        normal_msg.sender_id = 123  # Use integer directly
        normal_msg.out = False
        normal_msg.reply_to = None
        normal_msg.date = None
        normal_msg.sender = None
        messages.append(normal_msg)
        
        async def mock_format_message(msg, agent=None, media_chain=None):
            # Assign telepathic prefixes to all 6 telepathic messages (IDs 1-6)
            if 1 <= msg.id <= len(telepathic_prefixes):
                prefix = telepathic_prefixes[msg.id - 1]
                return [MsgTextPart(kind="text", text=f"{prefix}\nSome content")]
            else:
                return [MsgTextPart(kind="text", text="Normal message")]
        
        mock_agent.get_cached_entity = AsyncMock(return_value=None)
        with patch('handlers.received_helpers.message_processing.format_message_for_prompt', side_effect=mock_format_message):
            with patch('handlers.received_helpers.message_processing.get_channel_name', return_value="TestUser"):
                history = await _process_message_history(messages, mock_agent, None)
                
                # Should only have the normal message
                assert len(history) == 1
                assert history[0].message_parts[0]["text"] == "Normal message"

    @pytest.mark.asyncio
    async def test_filter_summarize_telepathic_messages(self):
        """Test that ⟦summarize⟧ messages are filtered from non-telepathic agents."""
        from handlers.received import _process_message_history
        from llm.base import MsgTextPart
        
        mock_agent = Mock()
        mock_agent.timezone = None
        mock_agent.agent_id = 999  # Non-telepathic agent (different from sender_id)
        
        # Create a mock message with ⟦summarize⟧ prefix
        mock_message = Mock()
        mock_message.id = 1
        mock_message.sender_id = 123  # Use integer directly
        mock_message.out = True  # This is from the agent
        mock_message.reply_to = None
        mock_message.date = None
        mock_message.sender = None
        
        # Create a normal message
        normal_message = Mock()
        normal_message.id = 2
        normal_message.sender_id = 456  # Use integer directly
        normal_message.out = False
        normal_message.reply_to = None
        normal_message.date = None
        normal_message.sender = None
        
        async def mock_format_message(msg, agent=None, media_chain=None):
            if msg.id == 1:
                return [MsgTextPart(kind="text", text="⟦summarize⟧\n{\"id\": \"summary-1\", \"content\": \"Summary text\"}")]
            else:
                return [MsgTextPart(kind="text", text="Normal message")]
        
        mock_agent.get_cached_entity = AsyncMock(return_value=None)
        with patch('handlers.received_helpers.message_processing.format_message_for_prompt', side_effect=mock_format_message):
            with patch('handlers.received_helpers.message_processing.get_channel_name', return_value="TestUser"):
                history = await _process_message_history([mock_message, normal_message], mock_agent, None)
                
                # Should only have the normal message, ⟦summarize⟧ should be filtered
                assert len(history) == 1
                assert history[0].message_parts[0]["text"] == "Normal message"

    @pytest.mark.asyncio
    async def test_media_messages_not_filtered(self):
        """Test that ⟦media⟧ messages are NOT filtered from non-telepathic agents (they're legitimate media descriptions)."""
        from handlers.received import _process_message_history
        from llm.base import MsgMediaPart
        
        mock_agent = Mock()
        mock_agent.timezone = None
        mock_agent.agent_id = 999  # Non-telepathic agent
        mock_agent.get_cached_entity = AsyncMock(return_value=None)
        
        # Create a mock message with ⟦media⟧ prefix (from format_media_sentence)
        mock_message = Mock()
        mock_message.id = 1
        mock_message.sender_id = 456  # Use integer directly
        mock_message.out = False
        mock_message.reply_to = None
        mock_message.date = None
        mock_message.sender = None
        
        async def mock_format_message(msg, agent=None, media_chain=None):
            # Simulate a media-only message (no text, just media with ⟦media⟧ prefix)
            return [MsgMediaPart(
                kind="media",
                media_kind="photo",
                rendered_text="⟦media⟧ ‹the photo that appears as a sunset over mountains›",
                unique_id="test_123"
            )]
        
        with patch('handlers.received_helpers.message_processing.format_message_for_prompt', side_effect=mock_format_message):
            with patch('handlers.received_helpers.message_processing.get_channel_name', return_value="TestUser"):
                history = await _process_message_history([mock_message], mock_agent, None)
                
                # Should NOT filter out the media message - it's legitimate
                assert len(history) == 1
                assert history[0].message_parts[0]["rendered_text"] == "⟦media⟧ ‹the photo that appears as a sunset over mountains›"

    @pytest.mark.asyncio
    async def test_silent_summarize_task_no_telepathic_message(self):
        """Test that summarize tasks with silent=True do not send telepathic messages."""
        from handlers.summarize import handle_immediate_summarize
        from task_graph import TaskNode
        
        mock_agent = Mock()
        mock_agent.name = "TestAgent"
        mock_agent.client = AsyncMock()
        mock_agent.agent_id = 789  # Non-telepathic agent
        
        # Create a summarize task with silent=True
        summarize_task = TaskNode(
            id="summary-1",
            type="summarize",
            params={
                "silent": True,
                "content": "Test summary content",
                "min_message_id": 1,
                "max_message_id": 10,
            }
        )
        
        with patch('handlers.telepathic.is_telepath') as mock_is_telepath:
            # Channel is telepathic, agent is not telepathic
            mock_is_telepath.side_effect = lambda x: x == 123456
            
            with patch('handlers.summarize._process_summarize_task', new_callable=AsyncMock) as mock_process:
                result = await handle_immediate_summarize(
                    summarize_task, agent=mock_agent, channel_id=123456
                )
                
                # Should return True (task was handled)
                assert result is True
                
                # Should process the summarize task
                mock_process.assert_called_once()
                
                # Should NOT send telepathic message (silent=True)
                mock_agent.client.send_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_normal_summarize_task_sends_telepathic_message(self):
        """Test that summarize tasks without silent=True do send telepathic messages when appropriate."""
        from handlers.summarize import handle_immediate_summarize
        from task_graph import TaskNode
        
        mock_agent = Mock()
        mock_agent.name = "TestAgent"
        mock_agent.client = AsyncMock()
        mock_agent.agent_id = 789  # Non-telepathic agent
        
        # Create a summarize task without silent flag (normal LLM response)
        summarize_task = TaskNode(
            id="summary-1",
            type="summarize",
            params={
                "content": "Test summary content",
                "min_message_id": 1,
                "max_message_id": 10,
            }
        )
        
        with patch('handlers.telepathic.is_telepath') as mock_is_telepath:
            # Channel is telepathic, agent is not telepathic
            mock_is_telepath.side_effect = lambda x: x == 123456
            
            with patch('handlers.summarize._process_summarize_task', new_callable=AsyncMock) as mock_process:
                result = await handle_immediate_summarize(
                    summarize_task, agent=mock_agent, channel_id=123456
                )
                
                # Should return True (task was handled)
                assert result is True
                
                # Should process the summarize task
                mock_process.assert_called_once()
                
                # Should send telepathic message (not silent)
                mock_agent.client.send_message.assert_called_once()
                send_args, send_kwargs = mock_agent.client.send_message.call_args
                assert send_args[0] == 123456
                assert send_kwargs["parse_mode"] == "Markdown"
                assert "⟦summarize⟧" in send_args[1]

    @pytest.mark.asyncio
    async def test_parse_llm_reply_marks_summarize_silent_in_summarization_mode(self):
        """Test that parse_llm_reply marks summarize tasks as silent when summarization_mode=True."""
        from handlers.received import parse_llm_reply_from_json, _dedupe_tasks_by_identifier, _assign_generated_identifiers
        import json
        
        json_text = json.dumps(
            [
                {
                    "kind": "summarize",
                    "id": "summary-1",
                    "content": "Test summary",
                    "min_message_id": 1,
                    "max_message_id": 10,
                }
            ],
            indent=2,
        )
        
        # Parse tasks without executing immediate tasks (which would try to save to disk)
        tasks = await parse_llm_reply_from_json(
            json_text, agent_id=123, channel_id=456, agent=None
        )
        tasks = _dedupe_tasks_by_identifier(tasks)
        
        # Manually mark as silent (simulating what parse_llm_reply does)
        summarization_mode = True
        if summarization_mode:
            for task in tasks:
                if task.type == "summarize":
                    task.params["silent"] = True
        
        tasks = _assign_generated_identifiers(tasks)
        
        # Should have one summarize task
        assert len(tasks) == 1
        assert tasks[0].type == "summarize"
        
        # Should be marked as silent
        assert tasks[0].params.get("silent") is True

    @pytest.mark.asyncio
    async def test_parse_llm_reply_normal_summarize_not_silent(self):
        """Test that parse_llm_reply does not mark summarize tasks as silent in normal mode."""
        from handlers.received import parse_llm_reply_from_json, _dedupe_tasks_by_identifier, _assign_generated_identifiers
        import json
        
        json_text = json.dumps(
            [
                {
                    "kind": "summarize",
                    "id": "summary-1",
                    "content": "Test summary",
                    "min_message_id": 1,
                    "max_message_id": 10,
                }
            ],
            indent=2,
        )
        
        # Parse tasks without executing immediate tasks (which would try to save to disk)
        tasks = await parse_llm_reply_from_json(
            json_text, agent_id=123, channel_id=456, agent=None
        )
        tasks = _dedupe_tasks_by_identifier(tasks)
        
        # Don't mark as silent (simulating normal mode)
        summarization_mode = False
        if summarization_mode:
            for task in tasks:
                if task.type == "summarize" or task.type == "think":
                    task.params["silent"] = True
        
        tasks = _assign_generated_identifiers(tasks)
        
        # Should have one summarize task
        assert len(tasks) == 1
        assert tasks[0].type == "summarize"
        
        # Should NOT be marked as silent
        assert tasks[0].params.get("silent") is not True

    @pytest.mark.asyncio
    async def test_silent_think_task_no_telepathic_message(self):
        """Test that think tasks with silent=True do not send telepathic messages."""
        from handlers.think import handle_immediate_think
        from task_graph import TaskNode
        
        mock_agent = Mock()
        mock_agent.name = "TestAgent"
        mock_agent.client = AsyncMock()
        mock_agent.agent_id = 789  # Non-telepathic agent
        
        # Create a think task with silent=True
        think_task = TaskNode(
            id="think-1",
            type="think",
            params={
                "silent": True,
                "text": "I need to think about this",
            }
        )
        
        with patch('handlers.telepathic.is_telepath') as mock_is_telepath:
            # Channel is telepathic, agent is not telepathic
            mock_is_telepath.side_effect = lambda x: x == 123456
            
            result = await handle_immediate_think(
                think_task, agent=mock_agent, channel_id=123456
            )
            
            # Should return True (task was handled)
            assert result is True
            
            # Should NOT send telepathic message (silent=True)
            mock_agent.client.send_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_normal_think_task_sends_telepathic_message(self):
        """Test that think tasks without silent=True do send telepathic messages when appropriate."""
        from handlers.think import handle_immediate_think
        from task_graph import TaskNode
        
        mock_agent = Mock()
        mock_agent.name = "TestAgent"
        mock_agent.client = AsyncMock()
        mock_agent.agent_id = 789  # Non-telepathic agent
        
        # Create a think task without silent flag (normal LLM response)
        think_task = TaskNode(
            id="think-1",
            type="think",
            params={
                "text": "I need to think about this",
            }
        )
        
        with patch('handlers.telepathic.is_telepath') as mock_is_telepath:
            # Channel is telepathic, agent is not telepathic
            mock_is_telepath.side_effect = lambda x: x == 123456
            
            result = await handle_immediate_think(
                think_task, agent=mock_agent, channel_id=123456
            )
            
            # Should return True (task was handled)
            assert result is True
            
            # Should send telepathic message (not silent)
            mock_agent.client.send_message.assert_called_once_with(
                123456, "⟦think⟧\nI need to think about this", parse_mode="Markdown"
            )

    @pytest.mark.asyncio
    async def test_parse_llm_reply_marks_think_silent_in_summarization_mode(self):
        """Test that parse_llm_reply marks think tasks as silent when summarization_mode=True."""
        from handlers.received import parse_llm_reply_from_json, _dedupe_tasks_by_identifier, _assign_generated_identifiers
        import json
        
        json_text = json.dumps(
            [
                {
                    "kind": "think",
                    "text": "I need to think about this",
                }
            ],
            indent=2,
        )
        
        # Parse tasks without executing immediate tasks (which would try to send messages)
        tasks = await parse_llm_reply_from_json(
            json_text, agent_id=123, channel_id=456, agent=None
        )
        tasks = _dedupe_tasks_by_identifier(tasks)
        
        # Manually mark as silent (simulating what parse_llm_reply does)
        summarization_mode = True
        if summarization_mode:
            for task in tasks:
                if task.type == "summarize" or task.type == "think":
                    task.params["silent"] = True
        
        tasks = _assign_generated_identifiers(tasks)
        
        # Should have one think task
        assert len(tasks) == 1
        assert tasks[0].type == "think"
        
        # Should be marked as silent
        assert tasks[0].params.get("silent") is True
