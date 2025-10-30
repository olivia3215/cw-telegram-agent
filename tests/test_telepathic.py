# tests/test_telepathic.py

# Copyright (c) 2025 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.

import pytest
from pathlib import Path
from unittest.mock import Mock, patch, AsyncMock

from telepathic import is_telepath, reload_telepathic_channels, _load_telepathic_channels


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
        from handlers.received import _maybe_send_telepathic_message
        
        mock_agent = Mock()
        mock_agent.name = "TestAgent"
        mock_agent.client = AsyncMock()
        mock_agent.agent_id = 789  # Non-telepathic agent
        
        with patch('handlers.received.is_telepath') as mock_is_telepath:
            # Channel is telepathic, agent is not telepathic
            mock_is_telepath.side_effect = lambda x: x == 123456
            
            await _maybe_send_telepathic_message(mock_agent, 123456, "⟦think⟧", "I need to think about this")
            
            mock_agent.client.send_message.assert_called_once_with(
                123456, "⟦think⟧\nI need to think about this", parse_mode="Markdown"
            )

    @pytest.mark.asyncio
    async def test_send_telepathic_message_empty_content(self):
        """Test that empty content is not sent."""
        from handlers.received import _maybe_send_telepathic_message
        
        mock_agent = Mock()
        mock_agent.client = AsyncMock()
        
        await _maybe_send_telepathic_message(mock_agent, 123456, "⟦think⟧", "")
        
        mock_agent.client.send_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_send_telepathic_message_error_handling(self):
        """Test error handling when sending telepathic message fails."""
        from handlers.received import _maybe_send_telepathic_message
        
        mock_agent = Mock()
        mock_agent.name = "TestAgent"
        mock_agent.client = AsyncMock()
        mock_agent.agent_id = 789  # Non-telepathic agent
        mock_agent.client.send_message.side_effect = Exception("Send failed")
        
        with patch('handlers.received.is_telepath') as mock_is_telepath:
            # Channel is telepathic, agent is not telepathic
            mock_is_telepath.side_effect = lambda x: x == 123456
            
            with patch('handlers.received.logger') as mock_logger:
                await _maybe_send_telepathic_message(mock_agent, 123456, "⟦think⟧", "test")
                
                mock_logger.error.assert_called_once()

    @pytest.mark.asyncio
    async def test_parse_llm_reply_think_telepathic(self):
        """Test that think tasks send telepathic messages when channel is telepathic and agent is not."""
        from handlers.received import parse_llm_reply_from_markdown
        
        mock_agent = Mock()
        mock_agent.name = "TestAgent"
        mock_agent.client = AsyncMock()
        mock_agent.agent_id = 123  # Non-telepathic agent
        
        md_text = """# «think»

I need to think about this problem carefully."""
        
        with patch('handlers.received.is_telepath') as mock_is_telepath:
            # Channel is telepathic, agent is not telepathic
            mock_is_telepath.side_effect = lambda x: x == 456
            
            tasks = await parse_llm_reply_from_markdown(
                md_text, agent_id=123, channel_id=456, agent=mock_agent
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
        from handlers.received import parse_llm_reply_from_markdown
        
        mock_agent = Mock()
        mock_agent.name = "TestAgent"
        mock_agent.client = AsyncMock()
        mock_agent.agent_id = 123  # Non-telepathic agent
        
        md_text = """# «remember»

User prefers short responses."""
        
        with patch('handlers.received.is_telepath') as mock_is_telepath:
            # Channel is telepathic, agent is not telepathic
            mock_is_telepath.side_effect = lambda x: x == 456
            
            with patch('handlers.received._process_remember_task', new_callable=AsyncMock) as mock_process:
                tasks = await parse_llm_reply_from_markdown(
                    md_text, agent_id=123, channel_id=456, agent=mock_agent
                )
                
                # Should not add remember task to task list
                assert len(tasks) == 0
                
                # Should send telepathic message
                mock_agent.client.send_message.assert_called_once_with(
                    456, "⟦remember⟧\nUser prefers short responses.", parse_mode="Markdown"
                )
                
                # Should still process the remember task
                mock_process.assert_called_once()

    @pytest.mark.asyncio
    async def test_parse_llm_reply_retrieve_telepathic(self):
        """Test that retrieve tasks send telepathic messages when channel is telepathic and agent is not."""
        from handlers.received import parse_llm_reply_from_markdown
        
        mock_agent = Mock()
        mock_agent.name = "TestAgent"
        mock_agent.client = AsyncMock()
        mock_agent.agent_id = 123  # Non-telepathic agent
        
        md_text = """# «retrieve»

https://example.com/page1
https://example.com/page2"""
        
        with patch('handlers.received.is_telepath') as mock_is_telepath:
            # Channel is telepathic, agent is not telepathic
            mock_is_telepath.side_effect = lambda x: x == 456
            
            tasks = await parse_llm_reply_from_markdown(
                md_text, agent_id=123, channel_id=456, agent=mock_agent
            )
            
            # Should add retrieve task to task list
            assert len(tasks) == 1
            assert tasks[0].type == "retrieve"
            assert tasks[0].params["urls"] == ["https://example.com/page1", "https://example.com/page2"]
            
            # Should send telepathic message
            mock_agent.client.send_message.assert_called_once_with(
                456, "⟦retrieve⟧\nhttps://example.com/page1\nhttps://example.com/page2", parse_mode="Markdown"
            )

    @pytest.mark.asyncio
    async def test_parse_llm_reply_non_telepathic_channel(self):
        """Test that telepathic messages are not sent for non-telepathic channels."""
        from handlers.received import parse_llm_reply_from_markdown
        
        mock_agent = Mock()
        mock_agent.name = "TestAgent"
        mock_agent.client = AsyncMock()
        mock_agent.agent_id = 123  # Non-telepathic agent
        
        md_text = """# «think»

I need to think about this."""
        
        with patch('handlers.received.is_telepath', return_value=False):
            tasks = await parse_llm_reply_from_markdown(
                md_text, agent_id=123, channel_id=456, agent=mock_agent
            )
            
            # Should not add think task to task list
            assert len(tasks) == 0
            
            # Should not send telepathic message
            mock_agent.client.send_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_parse_llm_reply_telepathic_agent_no_telepathic_message(self):
        """Test that telepathic agents do not send telepathic messages even to telepathic channels."""
        from handlers.received import parse_llm_reply_from_markdown
        
        mock_agent = Mock()
        mock_agent.name = "TestAgent"
        mock_agent.client = AsyncMock()
        mock_agent.agent_id = 123  # Telepathic agent
        
        md_text = """# «think»

I need to think about this."""
        
        with patch('handlers.received.is_telepath') as mock_is_telepath:
            # Both channel and agent are telepathic
            mock_is_telepath.side_effect = lambda x: x in [123, 456]
            
            tasks = await parse_llm_reply_from_markdown(
                md_text, agent_id=123, channel_id=456, agent=mock_agent
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
        mock_message1.sender_id = Mock()
        mock_message1.sender_id.user_id = 123
        mock_message1.out = False
        mock_message1.reply_to = None
        mock_message1.date = None
        
        mock_message2 = Mock()
        mock_message2.id = 2
        mock_message2.sender_id = Mock()
        mock_message2.sender_id.user_id = 123
        mock_message2.out = True  # This is from the agent
        mock_message2.reply_to = None
        mock_message2.date = None
        
        # Mock format_message_for_prompt to return different content
        async def mock_format_message(msg, agent=None, media_chain=None):
            if msg.id == 1:
                return [MsgTextPart(kind="text", text="Hello, how are you?")]
            elif msg.id == 2:
                return [MsgTextPart(kind="text", text="⟦think⟧\nI need to think about this")]
            return []
        
        with patch('handlers.received.format_message_for_prompt', side_effect=mock_format_message):
            with patch('handlers.received.get_channel_name', return_value="TestUser"):
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
        telepathic_prefixes = ["⟦think⟧", "⟦remember⟧", "⟦retrieve⟧"]
        
        for i, prefix in enumerate(telepathic_prefixes):
            mock_msg = Mock()
            mock_msg.id = i + 1
            mock_msg.sender_id = Mock()
            mock_msg.sender_id.user_id = 123
            mock_msg.out = True
            mock_msg.reply_to = None
            mock_msg.date = None
            messages.append(mock_msg)
        
        # Add one normal message
        normal_msg = Mock()
        normal_msg.id = 4
        normal_msg.sender_id = Mock()
        normal_msg.sender_id.user_id = 123
        normal_msg.out = False
        normal_msg.reply_to = None
        normal_msg.date = None
        messages.append(normal_msg)
        
        async def mock_format_message(msg, agent=None, media_chain=None):
            if msg.id <= 3:
                prefix = telepathic_prefixes[msg.id - 1]
                return [MsgTextPart(kind="text", text=f"{prefix}\nSome content")]
            else:
                return [MsgTextPart(kind="text", text="Normal message")]
        
        with patch('handlers.received.format_message_for_prompt', side_effect=mock_format_message):
            with patch('handlers.received.get_channel_name', return_value="TestUser"):
                history = await _process_message_history(messages, mock_agent, None)
                
                # Should only have the normal message
                assert len(history) == 1
                assert history[0].message_parts[0]["text"] == "Normal message"
