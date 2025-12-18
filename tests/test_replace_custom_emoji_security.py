# tests/test_replace_custom_emoji_security.py
#
# Security tests for _replace_custom_emoji_in_reactions function to prevent XSS attacks

import importlib.util
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest


# Load the conversation_get module directly (since agents/ is not a package)
_conversation_get_path = Path(__file__).parent.parent / "src" / "admin_console" / "agents" / "conversation_get.py"
_spec = importlib.util.spec_from_file_location("conversation_get", _conversation_get_path)
_conversation_get_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_conversation_get_module)
_replace_custom_emoji_in_reactions = _conversation_get_module._replace_custom_emoji_in_reactions


@pytest.mark.asyncio
async def test_escapes_user_name_in_reactions():
    """Test that user names with HTML characters are escaped to prevent XSS.
    
    This test verifies the fix for the XSS vulnerability where user_name
    (user-controlled) from get_channel_name() was inserted directly into the
    HTML string without escaping.
    """
    # Create mock objects
    mock_agent = MagicMock()
    mock_agent.name = "TestAgent"
    
    # Mock reaction with custom emoji
    mock_reaction_obj = MagicMock()
    mock_reaction_obj.document_id = 123456
    
    mock_reaction = MagicMock()
    mock_reaction.reaction = mock_reaction_obj
    
    # Mock peer_id
    mock_peer_id = MagicMock()
    mock_reaction.peer_id = mock_peer_id
    
    # Mock message with reactions
    mock_message = MagicMock()
    mock_reactions_obj = MagicMock()
    mock_reactions_obj.recent_reactions = [mock_reaction]
    mock_message.reactions = mock_reactions_obj
    mock_message.id = 999
    
    # Mock extract_user_id_from_peer to return a user ID
    from unittest.mock import patch
    with patch('utils.extract_user_id_from_peer', return_value=12345):
        # Mock get_channel_name to return malicious HTML
        with patch('handlers.received_helpers.message_processing.get_channel_name', new_callable=AsyncMock) as mock_get_name:
            mock_get_name.return_value = "<script>alert('XSS')</script>"
            
            # Mock get_custom_emoji_name
            with patch('utils.get_custom_emoji_name', new_callable=AsyncMock) as mock_get_emoji:
                mock_get_emoji.return_value = "test_emoji"
                
                result = await _replace_custom_emoji_in_reactions(
                    reactions_str="original reactions",
                    agent_name="TestAgent",
                    message_id="999",
                    message=mock_message,
                    agent=mock_agent
                )
                
                # Verify the function processed the reaction
                assert result, f"Result should not be empty, got: {result!r}"
                
                # HTML tags should be escaped
                assert "<script>" not in result, f"Found unescaped <script> tag in result: {result!r}"
                assert "&lt;script&gt;" in result, f"Expected escaped script tag, got: {result!r}"
                
                # The user name should be present (escaped)
                assert "alert" in result  # The script content should be present but escaped
                
                # The reaction structure should still be valid
                assert '"(12345)=' in result or 'data-document-id="123456"' in result, f"Invalid reaction structure: {result!r}"


@pytest.mark.asyncio
async def test_escapes_user_name_with_img_tag():
    """Test that user names with <img> tags are escaped."""
    mock_agent = MagicMock()
    mock_agent.name = "TestAgent"
    
    mock_reaction_obj = MagicMock()
    mock_reaction_obj.document_id = 123456
    
    mock_reaction = MagicMock()
    mock_reaction.reaction = mock_reaction_obj
    mock_peer_id = MagicMock()
    mock_reaction.peer_id = mock_peer_id
    
    mock_message = MagicMock()
    mock_reactions_obj = MagicMock()
    mock_reactions_obj.recent_reactions = [mock_reaction]
    mock_message.reactions = mock_reactions_obj
    mock_message.id = 999
    
    from unittest.mock import patch
    with patch('utils.extract_user_id_from_peer', return_value=12345):
        with patch('handlers.received_helpers.message_processing.get_channel_name', new_callable=AsyncMock) as mock_get_name:
            mock_get_name.return_value = '<img src=x onerror=alert("XSS")>'
            
            with patch('utils.get_custom_emoji_name', new_callable=AsyncMock) as mock_get_emoji:
                mock_get_emoji.return_value = "test_emoji"
                
                result = await _replace_custom_emoji_in_reactions(
                    reactions_str="original reactions",
                    agent_name="TestAgent",
                    message_id="999",
                    message=mock_message,
                    agent=mock_agent
                )
                
                # <img> tag should be escaped (we're looking for the user name, not the emoji img tag)
                # The user name should be in the format: "<malicious_name>(12345)="
                # We should NOT see an unescaped <img tag from the user name
                # But we SHOULD see an img tag from the emoji itself
                assert 'data-document-id="123456"' in result  # Emoji img tag should be present
                # The malicious img tag from user name should be escaped
                assert '&lt;img' in result or result.count('<img') == 1  # Only one img tag (the emoji)


@pytest.mark.asyncio
async def test_escapes_user_name_with_quotes():
    """Test that user names with quotes are escaped."""
    mock_agent = MagicMock()
    mock_agent.name = "TestAgent"
    
    mock_reaction_obj = MagicMock()
    mock_reaction_obj.document_id = 123456
    
    mock_reaction = MagicMock()
    mock_reaction.reaction = mock_reaction_obj
    mock_peer_id = MagicMock()
    mock_reaction.peer_id = mock_peer_id
    
    mock_message = MagicMock()
    mock_reactions_obj = MagicMock()
    mock_reactions_obj.recent_reactions = [mock_reaction]
    mock_message.reactions = mock_reactions_obj
    mock_message.id = 999
    
    from unittest.mock import patch
    with patch('utils.extract_user_id_from_peer', return_value=12345):
        with patch('handlers.received_helpers.message_processing.get_channel_name', new_callable=AsyncMock) as mock_get_name:
            mock_get_name.return_value = 'User with "quotes"'
            
            with patch('utils.get_custom_emoji_name', new_callable=AsyncMock) as mock_get_emoji:
                mock_get_emoji.return_value = "test_emoji"
                
                result = await _replace_custom_emoji_in_reactions(
                    reactions_str="original reactions",
                    agent_name="TestAgent",
                    message_id="999",
                    message=mock_message,
                    agent=mock_agent
                )
                
                # Quotes should be escaped as &quot;
                assert '&quot;quotes&quot;' in result or '"quotes"' not in result or '&quot;' in result
