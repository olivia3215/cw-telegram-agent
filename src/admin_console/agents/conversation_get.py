# admin_console/agents/conversation_get.py
#
# Route handler for getting conversation history.

import asyncio
import html
import logging
from pathlib import Path
from typing import Any

from flask import jsonify  # pyright: ignore[reportMissingImports]

from admin_console.helpers import get_agent_by_name
from config import STATE_DIRECTORY
from handlers.received_helpers.message_processing import format_message_reactions
from memory_storage import load_property_entries
from media.media_injector import format_message_for_prompt
from media.media_source import get_default_media_source_chain
from utils.telegram import can_agent_send_to_channel, get_channel_name, is_dm, is_user_blocking_agent
from telethon.tl.functions.stories import GetStoriesByIDRequest  # pyright: ignore[reportMissingImports]

# Import markdown_to_html - use importlib since this module is loaded dynamically by conversation_content.py
import importlib.util
from pathlib import Path
_conversation_path = Path(__file__).parent / "conversation.py"
_conversation_spec = importlib.util.spec_from_file_location("conversation", _conversation_path)
_conversation_mod = importlib.util.module_from_spec(_conversation_spec)
_conversation_spec.loader.exec_module(_conversation_mod)
markdown_to_html = _conversation_mod.markdown_to_html

# Import entity formatting utilities
from utils.telegram_entities import utf16_offset_to_python_index, entities_to_markdown

logger = logging.getLogger(__name__)


def _utf16_offset_to_python_index(text: str, utf16_offset: int) -> int:
    """
    Convert a UTF-16 code unit offset to a Python string index.
    
    DEPRECATED: Use utils.telegram_entities.utf16_offset_to_python_index instead.
    This is kept as a wrapper for backward compatibility within this module.
    """
    return utf16_offset_to_python_index(text, utf16_offset)


def _entities_to_markdown(text: str, entities: list) -> str:
    """
    Convert Telegram message entities to markdown format.
    
    DEPRECATED: Use utils.telegram_entities.entities_to_markdown instead.
    This is kept as a wrapper for backward compatibility within this module.
    """
    return entities_to_markdown(text, entities)


async def _replace_custom_emojis_with_images(
    html_text: str, 
    text: str, 
    entities: list, 
    agent_name: str, 
    message_id: str,
    message: Any = None
) -> str:
    """
    Replace custom emoji characters in HTML with img tags that display the custom emoji images.
    This is a central helper that can be used for both message text and reactions.
    
    
    Args:
        html_text: HTML text that may contain custom emoji characters
        text: Original text (to match emoji positions)
        entities: List of MessageEntity objects (should include MessageEntityCustomEmoji)
        agent_name: Agent name for building emoji URLs
        message_id: Message ID for building emoji URLs
        message: Optional Telegram message object to extract document references from
        
    Returns:
        HTML with custom emojis replaced by img tags
    """
    if not html_text or not entities:
        return html_text
    
    # Quick check: if the HTML already contains custom-emoji-container tags,
    # we might be processing already-processed HTML. 
    # Count how many emoji characters from entities are still in the HTML
    emoji_entities = [e for e in entities if e.__class__.__name__ == "MessageEntityCustomEmoji"]
    if 'custom-emoji-container' in html_text and emoji_entities:
        # Check if any emoji characters from entities are still present in HTML
        # If not, all emojis are already replaced and we should skip
        emoji_chars_in_html = []
        for entity in emoji_entities[:5]:  # Check first 5 as sample
            utf16_offset = getattr(entity, "offset", 0)
            utf16_length = getattr(entity, "length", 0)
            start_idx = utf16_offset_to_python_index(text, utf16_offset)
            end_idx = utf16_offset_to_python_index(text, utf16_offset + utf16_length)
            emoji_char = text[start_idx:end_idx]
            if emoji_char in html_text:
                emoji_chars_in_html.append(emoji_char)
        
        if not emoji_chars_in_html:
            # All emojis already replaced, skip to avoid duplication
            return html_text
        else:
            container_count = html_text.count('custom-emoji-container')
            logger.debug(f"_replace_custom_emojis_with_images: HTML already contains {container_count} custom-emoji-container tags for message {message_id}, but {len(emoji_chars_in_html)} emoji characters still present. Processing may cause duplication.")
    
    # Build a map of document_id -> document for custom emojis
    # We can get documents from the message's document attribute or from entities
    emoji_documents = {}
    if message:
        # Check if message has a document attribute (for media messages)
        msg_doc = getattr(message, "document", None)
        if msg_doc:
            doc_id = getattr(msg_doc, "id", None)
            if doc_id:
                emoji_documents[doc_id] = msg_doc
    
    result = html_text
    # Process custom emoji entities in reverse order to maintain positions
    # This way, when we replace, earlier positions aren't affected by length changes
    
    # First, deduplicate entities - if multiple entities point to the same position and document_id,
    # we only need to process one of them
    emoji_entities = [e for e in entities if e.__class__.__name__ == "MessageEntityCustomEmoji"]
    seen_entities = {}
    for entity in emoji_entities:
        utf16_offset = getattr(entity, "offset", 0)
        utf16_length = getattr(entity, "length", 0)
        document_id = getattr(entity, "document_id", None)
        if document_id:
            # Convert to Python indices for deduplication
            start_idx = utf16_offset_to_python_index(text, utf16_offset)
            end_idx = utf16_offset_to_python_index(text, utf16_offset + utf16_length)
            entity_key = (start_idx, end_idx, document_id)
            # Keep the first entity we see for each unique position+document_id
            if entity_key not in seen_entities:
                seen_entities[entity_key] = entity
    
    # Now sort the deduplicated entities
    sorted_entities = sorted(
        seen_entities.values(),
        key=lambda e: (getattr(e, "offset", 0), -getattr(e, "length", 0)),
        reverse=True
    )
    
    logger.debug(f"_replace_custom_emojis_with_images: Processing {len(sorted_entities)} unique emoji entities (from {len(emoji_entities)} total) for message {message_id}")
    
    # Track which positions in the original text have been replaced
    # This prevents replacing the same emoji multiple times if it appears multiple times
    replaced_positions = set()
    
    for entity in sorted_entities:
        utf16_offset = getattr(entity, "offset", 0)
        utf16_length = getattr(entity, "length", 0)
        document_id = getattr(entity, "document_id", None)
        
        if not document_id:
            continue
        
        # Convert UTF-16 offsets to Python string indices
        start_idx = _utf16_offset_to_python_index(text, utf16_offset)
        end_idx = _utf16_offset_to_python_index(text, utf16_offset + utf16_length)
        
        # Skip if we've already replaced an emoji at this position
        position_key = (start_idx, end_idx)
        if position_key in replaced_positions:
            logger.debug(f"Skipping duplicate emoji entity at position {start_idx}-{end_idx} for document_id {document_id}")
            continue
        
        emoji_char = text[start_idx:end_idx]
        
        # Replace the emoji character in the HTML with an img tag or Lottie container
        # Use the media serving endpoint pattern - we'll create an emoji endpoint
        # The blueprint is registered with url_prefix="/admin", so /api/agents/... becomes /admin/api/agents/...
        # But we need to include /admin in the URL since img src is resolved from document root
        emoji_url = f"/admin/api/agents/{agent_name}/emoji/{document_id}"
        
        # Escape emoji_char for safe use in HTML attributes
        emoji_char_escaped = html.escape(emoji_char)
        
        # Create a container that can handle both static and animated emojis
        # The frontend JavaScript will detect TGS files and render them with Lottie
        # For now, use a span with data attributes that the frontend can process
        emoji_tag = f'<span class="custom-emoji-container" data-document-id="{document_id}" data-emoji-url="{emoji_url}" data-emoji-char="{emoji_char_escaped}" style="display: inline-block; width: 1.2em; height: 1.2em; vertical-align: middle;"><img src="{emoji_url}" alt="{emoji_char_escaped}" class="custom-emoji-img" style="width: 1.2em; height: 1.2em; vertical-align: middle; display: inline-block;" onerror="this.parentElement.classList.add(\'emoji-load-error\')" /></span>'
        
        # Find and replace the emoji in the HTML at the specific position
        # The challenge: HTML has tags inserted (like <strong>, <em>), so positions don't match exactly.
        # Strategy: Count how many times this emoji_char appears in the original text up to this position,
        # then find the Nth occurrence in the HTML.
        
        # Count occurrences of this emoji_char in the original text up to start_idx
        occurrences_before = text[:start_idx].count(emoji_char)
        
        # Find the (occurrences_before + 1)th occurrence in the HTML
        # This should correspond to the emoji at this position
        # IMPORTANT: We must skip occurrences that are inside HTML tags (like in alt attributes)
        search_start = 0
        occurrence_count = 0
        emoji_pos = -1
        
        def is_inside_html_tag(html_str: str, pos: int) -> bool:
            """Check if position pos is inside an HTML tag (between < and >)"""
            # Look backwards for the nearest < or >
            last_open = html_str.rfind('<', 0, pos)
            last_close = html_str.rfind('>', 0, pos)
            # If we found a < and it's after the last >, we're inside a tag
            if last_open != -1 and (last_close == -1 or last_open > last_close):
                # Check if this tag closes before our position
                tag_close = html_str.find('>', last_open)
                if tag_close == -1 or tag_close > pos:
                    return True
            return False
        
        while occurrence_count <= occurrences_before:
            pos = result.find(emoji_char, search_start)
            if pos == -1:
                break
            
            # Skip if this occurrence is inside an HTML tag (e.g., in alt="..." attribute)
            if not is_inside_html_tag(result, pos):
                occurrence_count += 1
                if occurrence_count == occurrences_before + 1:
                    emoji_pos = pos
                    break
            
            search_start = pos + 1
        
        if emoji_pos != -1:
            # Check if this position has already been replaced
            # Look backwards from the emoji position to see if there's already a custom-emoji-container
            # that was inserted at this location. We check up to 500 chars back to find the opening tag.
            check_start = max(0, emoji_pos - 500)
            check_region = result[check_start:emoji_pos]
            
            # Look for a custom-emoji-container that ends right before our emoji position
            # This would indicate the emoji at this position was already replaced
            container_end_pattern = f'</span>'
            last_container_end = check_region.rfind(container_end_pattern)
            if last_container_end != -1:
                # Found a closing tag, check if it's a custom-emoji-container with our document_id
                container_start = check_region.rfind('<span class="custom-emoji-container"', 0, last_container_end)
                if container_start != -1:
                    container_section = check_region[container_start:last_container_end + len(container_end_pattern)]
                    if f'data-document-id="{document_id}"' in container_section:
                        # This emoji was already replaced - the container ends right before our position
                        logger.info(f"Skipping emoji at text position {start_idx}-{end_idx} - already replaced in HTML (found container ending at HTML position {check_start + last_container_end}) for document_id {document_id} in message {message_id}")
                        replaced_positions.add(position_key)
                        continue
            
            # Also check if we've already processed this exact position
            if position_key in replaced_positions:
                logger.debug(f"Skipping emoji at text position {start_idx}-{end_idx} - position already in replaced_positions")
                continue
            
            # Before replacing, verify the emoji character is actually at this position
            actual_char = result[emoji_pos:emoji_pos + len(emoji_char)]
            if actual_char != emoji_char:
                logger.warning(f"Emoji character mismatch at HTML position {emoji_pos}: expected '{emoji_char}', found '{actual_char}' for document_id {document_id} in message {message_id}")
                continue
            
            # Replace at this specific position - this removes the emoji character and inserts the tag
            result = result[:emoji_pos] + emoji_tag + result[emoji_pos + len(emoji_char):]
            replaced_positions.add(position_key)
            logger.debug(f"Replaced emoji '{emoji_char}' at text position {start_idx}-{end_idx} (HTML position {emoji_pos}) for document_id {document_id} in message {message_id}")
        else:
            logger.warning(f"Could not find emoji character '{emoji_char}' in HTML (looking for occurrence {occurrences_before + 1}) for document_id {document_id} at text position {start_idx}-{end_idx} in message {message_id}. HTML length: {len(result)}, HTML preview: {result[:200]}")
    
    return result


async def _replace_custom_emoji_in_reactions(
    reactions_str: str,
    agent_name: str,
    message_id: str,
    message: Any,
    agent: Any
) -> str:
    """
    Replace custom emoji text (like "[name]") in reactions with img tags.
    
    Args:
        reactions_str: Formatted reactions string like '"User"(123)=[emoji_name]'
        agent_name: Agent name for building emoji URLs
        message_id: Message ID for building emoji URLs
        message: Telegram message object to extract reaction entities
        agent: Agent instance for getting emoji names
        
    Returns:
        Reactions string with custom emojis replaced by img tags
    """
    if not reactions_str:
        return reactions_str
    
    logger.debug(f"Admin console: Processing reactions for message {message_id}")
    
    try:
        reactions_obj = getattr(message, 'reactions', None)
        if not reactions_obj:
            logger.debug(f"Admin console: Message {message_id} has no reactions object")
            return reactions_str
        
        recent_reactions = getattr(reactions_obj, 'recent_reactions', None)
        if not recent_reactions:
            logger.debug(f"Admin console: Message {message_id} has no recent_reactions")
            return reactions_str
        
        logger.debug(f"Admin console: Message {message_id} has {len(recent_reactions)} reaction(s)")
        
        from utils import get_custom_emoji_name, extract_user_id_from_peer
        from handlers.received_helpers.message_processing import get_channel_name
        
        # Rebuild reactions string directly from reaction objects, using img tags for custom emojis
        # This is more reliable than trying to match and replace text in the formatted string
        reaction_parts = []
        for idx, reaction in enumerate(recent_reactions):
            # Get user info
            peer_id = getattr(reaction, 'peer_id', None)
            if not peer_id:
                logger.debug(f"Reaction {idx} on message {message_id} (admin) has no peer_id")
                continue
            
            user_id = extract_user_id_from_peer(peer_id)
            if user_id is None:
                logger.debug(f"Reaction {idx} on message {message_id} (admin) has no user_id")
                continue
            
            # Get user name
            try:
                user_name = await get_channel_name(agent, user_id)
            except Exception:
                user_name = str(user_id)
            
            logger.debug(f"Reaction {idx} on message {message_id} (admin) from {user_name}({user_id})")
            
            # Escape user_name to prevent XSS when inserted into HTML
            user_name_escaped = html.escape(user_name)
            
            # Get reaction emoji
            reaction_obj = getattr(reaction, 'reaction', None)
            if not reaction_obj:
                logger.debug(f"Reaction {idx} on message {message_id} (admin) has no reaction object")
                continue
            
            # Build the reaction part
            reaction_part = f'"{user_name_escaped}"({user_id})='
            
            # Check if it's a custom emoji (has document_id)
            if hasattr(reaction_obj, 'document_id'):
                document_id = reaction_obj.document_id
                logger.debug(f"Reaction {idx} on message {message_id} (admin) is custom emoji with document_id={document_id}")
                # Build img tag directly for custom emoji - always use img tag, not text replacement
                emoji_url = f"/admin/api/agents/{agent_name}/emoji/{document_id}"
                # Try to get emoji name for alt text, but don't fail if we can't
                try:
                    emoji_name = await get_custom_emoji_name(agent, document_id)
                    logger.debug(f"Reaction {idx} on message {message_id} (admin) custom emoji name: {emoji_name[:100]}")
                    alt_text = emoji_name if emoji_name else "🎭"
                except Exception as e:
                    logger.debug(f"Reaction {idx} on message {message_id} (admin) failed to get emoji name: {e}")
                    alt_text = "🎭"
                # Escape alt_text for safe use in HTML attributes
                alt_text_escaped = html.escape(alt_text)
                # Add data attributes so the frontend can detect and handle animated emojis
                img_tag = f'<img class="custom-emoji-reaction" src="{emoji_url}" alt="{alt_text_escaped}" data-emoji-url="{emoji_url}" data-document-id="{document_id}" style="width: 1.2em; height: 1.2em; vertical-align: middle; display: inline-block;" />'
                reaction_part += img_tag
            elif hasattr(reaction_obj, 'emoticon'):
                # Regular emoji - keep as is
                logger.debug(f"Reaction {idx} on message {message_id} (admin) is standard emoji: {reaction_obj.emoticon}")
                reaction_part += reaction_obj.emoticon
            else:
                # Unknown reaction type - skip
                logger.debug(f"Reaction {idx} on message {message_id} (admin) has unknown type")
                continue
            
            reaction_parts.append(reaction_part)
        
        # Return rebuilt reactions string with img tags for custom emojis
        if reaction_parts:
            result = ', '.join(reaction_parts)
            logger.debug(f"Message {message_id} (admin) reactions rebuilt: {result[:100]}")
            return result
        else:
            # If we couldn't rebuild (shouldn't happen), return original
            logger.debug(f"Message {message_id} (admin) couldn't rebuild reactions, returning original")
            return reactions_str
    except Exception as e:
        logger.debug(f"Error replacing custom emojis in reactions: {e}")
        return reactions_str


def _get_highest_summarized_message_id_for_api(agent_config_name: str, channel_id: int) -> int | None:
    """
    Get the highest message ID that has been summarized (for use in Flask context).
    
    Everything with message ID <= this value can be assumed to be summarized.
    Returns None if no summaries exist.
    """
    try:
        from admin_console.helpers import get_agent_by_name
        
        agent = get_agent_by_name(agent_config_name)
        if not agent or not agent.is_authenticated:
            return None
        
        # Load from MySQL
        from db import summaries as db_summaries
        summaries = db_summaries.load_summaries(agent.agent_id, channel_id)
        
        highest_max_id = None
        for summary in summaries:
            max_id = summary.get("max_message_id")
            if max_id is not None:
                try:
                    max_id_int = int(max_id)
                    if highest_max_id is None or max_id_int > highest_max_id:
                        highest_max_id = max_id_int
                except (ValueError, TypeError):
                    pass
        return highest_max_id
    except Exception as e:
        logger.debug(f"Failed to get highest summarized message ID for {agent_config_name}/{channel_id}: {e}")
        return None


def api_get_conversation(agent_config_name: str, user_id: str):
    """Get conversation history (unsummarized messages only) and summaries."""
    try:
        agent = get_agent_by_name(agent_config_name)
        if not agent:
            return jsonify({"error": f"Agent '{agent_config_name}' not found"}), 404

        if not agent.client or not agent.client.is_connected():
            return jsonify({"error": "Agent client not connected"}), 503

        # Resolve user_id (which may be a username) to channel_id
        from admin_console.helpers import resolve_user_id_and_handle_errors
        channel_id, error_response = resolve_user_id_and_handle_errors(agent, user_id, logger)
        if error_response:
            return error_response

        # Get summaries from MySQL
        if not agent.is_authenticated:
            return jsonify({"error": "Agent not authenticated"}), 503
        
        from db import summaries as db_summaries
        summaries = db_summaries.load_summaries(agent.agent_id, channel_id)
        
        summaries.sort(key=lambda x: (x.get("min_message_id", 0), x.get("max_message_id", 0)))
        
        # Get highest summarized message ID to filter messages
        highest_summarized_id = _get_highest_summarized_message_id_for_api(agent.config_name, channel_id)

        # Get conversation history from Telegram
        # Check if agent's event loop is accessible before creating coroutine
        # This prevents RuntimeWarning about unawaited coroutines if execute() fails
        try:
            client_loop = agent._get_client_loop()
            if not client_loop or not client_loop.is_running():
                raise RuntimeError("Agent client event loop is not accessible or not running")
        except Exception as e:
            logger.warning(f"Cannot fetch conversation - event loop check failed: {e}")
            return jsonify({"error": "Agent client event loop is not available"}), 503
        
        # This is async, so we need to run it in the client's event loop
        # Cache for custom emoji documents (document_id -> document object)
        # This allows us to download emojis later without needing to fetch them again
        emoji_document_cache: dict[int, Any] = {}
        
        async def _check_blocked_status() -> bool:
            """Check if the conversation is blocked (agent cannot send messages to this channel)."""
            try:
                # Check if the agent blocked the user (using blocklist with ttl_seconds=0 to force fresh check)
                # This is the most reliable check - the blocklist API directly tells us if the agent blocked the user
                agent_blocked_user = False
                api_cache = agent.api_cache
                if api_cache:
                    agent_blocked_user = await api_cache.is_blocked(channel_id, ttl_seconds=0)
                
                # Check if the user blocked the agent using profile indicators
                user_blocked_agent = await is_user_blocking_agent(agent, channel_id)
                
                # Conversation is blocked if either party has blocked the other
                is_blocked = agent_blocked_user or user_blocked_agent
                return is_blocked
            except Exception as e:
                logger.warning(f"Error checking blocked status for {agent_config_name}/{channel_id}: {e}", exc_info=True)
                # On error, default to not blocked (to avoid false positives)
                return False
        
        async def _get_messages():
            try:
                # Use client.get_entity() directly since we're already in the client's event loop
                # This avoids event loop mismatch issues with agent.get_cached_entity()
                client = agent.client
                entity = await client.get_entity(channel_id)
                if not entity:
                    return []
                
                # Check if this is a DM conversation (needed for read status checking)
                is_dm_conversation = is_dm(entity)
                
                # For DMs, get the dialog to check read_outbox_max_id for read receipts
                # This tells us up to which message ID the partner has read our outgoing messages
                read_outbox_max_id = None
                if is_dm_conversation:
                    try:
                        # Get the dialog from the dialogs list to access read_outbox_max_id
                        dialogs = await client.get_dialogs()
                        for d in dialogs:
                            if d.id == channel_id:
                                # read_outbox_max_id is on the underlying TL Dialog object, not the wrapper
                                read_outbox_max_id = getattr(d.dialog, "read_outbox_max_id", None)
                                break
                    except Exception as e:
                        logger.debug(f"Failed to get dialog read_outbox_max_id for {channel_id}: {e}")
                
                # Get media chain for formatting media descriptions
                media_chain = get_default_media_source_chain()
                
                # Use min_id to only fetch unsummarized messages (avoid fetching messages we'll filter out)
                # This prevents unnecessary API calls and flood waits
                iter_kwargs = {"limit": 500}
                if highest_summarized_id is not None:
                    iter_kwargs["min_id"] = highest_summarized_id
                
                messages = []
                total_fetched = 0
                async for message in client.iter_messages(entity, **iter_kwargs):
                    total_fetched += 1
                    # All messages fetched should be unsummarized (min_id filters them)
                    # But double-check just in case
                    msg_id = int(message.id)
                    if highest_summarized_id is not None and msg_id <= highest_summarized_id:
                        # This shouldn't happen if min_id is working correctly, but log if it does
                        logger.warning(
                            f"[{agent_config_name}] Unexpected: message {msg_id} <= highest_summarized_id {highest_summarized_id} "
                            f"despite min_id filter"
                        )
                        continue
                    
                    # Try multiple ways to extract sender_id (for compatibility with different message types)
                    from_id = getattr(message, "from_id", None)
                    sender_id = None
                    if from_id:
                        sender_id = getattr(from_id, "user_id", None) or getattr(from_id, "channel_id", None)
                    
                    # Fallback: try message.sender.id if from_id didn't work
                    if not sender_id:
                        sender = getattr(message, "sender", None)
                        if sender:
                            sender_id = getattr(sender, "id", None)
                    
                    is_from_agent = sender_id == agent.agent_id
                    
                    # Get sender name - ensure it's never None so frontend can display it properly
                    # The frontend expects format: sender_name (sender_id), so we provide the name part
                    # get_channel_name should always return a non-empty string, but we handle failures gracefully
                    sender_name = None
                    if sender_id and isinstance(sender_id, int):
                        try:
                            sender_name = await get_channel_name(agent, sender_id)
                            # get_channel_name should never return None or empty, but be defensive
                            if not sender_name or not sender_name.strip():
                                # This shouldn't happen, but if it does, use a fallback
                                sender_name = "User"
                        except Exception as e:
                            logger.warning(f"Failed to get sender name for {sender_id}: {e}")
                            # Fallback: use generic name (frontend will append ID)
                            sender_name = "User"
                    elif sender_id:
                        # sender_id exists but isn't an int - use generic name
                        sender_name = "User"
                    else:
                        # No sender_id - this shouldn't happen for regular messages, but handle gracefully
                        # For messages without sender_id, we can't show the ID, so just show "User"
                        sender_name = "User"
                    
                    # Final safety check: ensure sender_name is never None
                    if not sender_name:
                        sender_name = "User"
                    
                    # Escape sender_name to prevent XSS when displayed in frontend
                    # (Frontend also escapes, but defense in depth)
                    sender_name = html.escape(sender_name)
                    
                    timestamp = message.date.isoformat() if hasattr(message, "date") and message.date else None
                    
                    # Extract reply_to information
                    reply_to_msg_id = None
                    reply_to = getattr(message, "reply_to", None)
                    if reply_to:
                        reply_to_msg_id_val = getattr(reply_to, "reply_to_msg_id", None)
                        if reply_to_msg_id_val is not None:
                            reply_to_msg_id = str(reply_to_msg_id_val)
                    
                    # Format reactions
                    reactions_str = await format_message_reactions(agent, message)
                    # Replace custom emojis in reactions with images
                    if reactions_str:
                        reactions_str = await _replace_custom_emoji_in_reactions(
                            reactions_str, agent_config_name, str(message.id), message, agent
                        )
                    
                    # Format media/stickers
                    message_parts = await format_message_for_prompt(message, agent=agent, media_chain=media_chain)
                    
                    # Check if this is a forwarded story
                    # Forwarded stories are represented by MessageMediaStory in the media attribute
                    # They may also have fwd_from with StoryFwdHeader, but the primary indicator is MessageMediaStory
                    fwd_from = getattr(message, "fwd_from", None)
                    media_attr = getattr(message, "media", None)
                    is_forwarded_story = False
                    story_from_name = None
                    story_id = None
                    
                    # Check media attribute for MessageMediaStory
                    story_item = None
                    story_peer = None
                    if media_attr:
                        media_class_name = media_attr.__class__.__name__ if hasattr(media_attr, "__class__") else None
                        media_str = str(type(media_attr)) if media_attr else ""
                        if media_class_name == "MessageMediaStory" or "MessageMediaStory" in media_str:
                            is_forwarded_story = True
                            story_id = getattr(media_attr, "id", None)
                            # Get the story item if available (contains the actual media)
                            story_item = getattr(media_attr, "story", None)
                            # Get the peer (user/channel) that posted the story
                            story_peer = getattr(media_attr, "peer", None)
                            if story_peer:
                                try:
                                    peer_id = getattr(story_peer, "user_id", None) or getattr(story_peer, "channel_id", None)
                                    if peer_id:
                                        story_from_name = await get_channel_name(agent, peer_id)
                                except Exception:
                                    pass
                            
                            # If story_item is None, try to fetch it from Telegram
                            if not story_item and story_id is not None and story_peer:
                                try:
                                    # Fetch the story
                                    stories_result = await client(GetStoriesByIDRequest(
                                        peer=story_peer,
                                        id=[story_id]
                                    ))
                                    # Extract the story from the result
                                    if hasattr(stories_result, "stories") and stories_result.stories:
                                        story_item = stories_result.stories[0]
                                except Exception as e:
                                    logger.debug(f"Failed to fetch story {story_id} for message {message.id}: {e}")
                    
                    # Also check fwd_from for StoryFwdHeader (alternative representation)
                    if fwd_from and not is_forwarded_story:
                        # Check if this is a StoryFwdHeader (forwarded story)
                        # StoryFwdHeader has story_id attribute, regular MessageFwdHeader doesn't
                        fwd_story_id = getattr(fwd_from, "story_id", None)
                        fwd_class_name = fwd_from.__class__.__name__ if hasattr(fwd_from, "__class__") else None
                        fwd_str = str(type(fwd_from)) if fwd_from else ""
                        
                        if fwd_story_id is not None or fwd_class_name == "StoryFwdHeader" or "StoryFwdHeader" in fwd_str:
                            is_forwarded_story = True
                            story_id = fwd_story_id
                            logger.debug(f"Detected forwarded story via StoryFwdHeader in message {message.id}, story_id={story_id}, class={fwd_class_name}")
                            # Try to get the name of the user who posted the story
                            from_peer = getattr(fwd_from, "from", None)
                            from_name = getattr(fwd_from, "from_name", None)
                            if from_name:
                                story_from_name = from_name
                            elif from_peer:
                                try:
                                    from_id = getattr(from_peer, "user_id", None) or getattr(from_peer, "channel_id", None)
                                    if from_id:
                                        story_from_name = await get_channel_name(agent, from_id)
                                except Exception:
                                    pass
                    
                    # Extract custom emoji documents from message entities and cache them
                    # This allows us to download emojis later
                    message_entities = getattr(message, "entities", None) or []
                    for entity in message_entities:
                        if entity.__class__.__name__ == "MessageEntityCustomEmoji":
                            doc_id = getattr(entity, "document_id", None)
                            if doc_id and doc_id not in emoji_document_cache:
                                # Try to get the document from the message
                                # Custom emojis might be in message.document or we need to fetch them
                                # For now, we'll try to get it from GetMessages or cache it when available
                                # Actually, custom emojis in text aren't in message.document
                                # We'll need to fetch them separately or use a different approach
                                pass
                    
                    # Get text with formatting preserved (markdown)
                    # Use text_markdown to preserve bold, italic, etc.
                    text_markdown = getattr(message, "text_markdown", None)
                    raw_text = getattr(message, "message", None) or getattr(message, "text", None) or ""
                    entities = getattr(message, "entities", None) or []
                    
                    # For forwarded stories, check if the message itself has text with formatting
                    # (the story caption will be handled separately)
                    
                    # Check if we have formatting information
                    has_entities = bool(entities)
                    has_text_markdown = bool(text_markdown and text_markdown != raw_text)
                    
                    if not text_markdown or text_markdown == raw_text:
                        # text_markdown not available or same as plain text - try entities
                        if raw_text and entities:
                            # Convert entities to markdown first, then to HTML
                            text_markdown = _entities_to_markdown(raw_text, entities)
                        else:
                            text_markdown = raw_text
                    
                    # Convert markdown to HTML for frontend display
                    text = markdown_to_html(text_markdown)
                    
                    # Replace custom emojis with images (pass message for document extraction)
                    text = await _replace_custom_emojis_with_images(
                        text, raw_text, entities, agent_config_name, str(message.id), message
                    )
                    
                    # If this is a forwarded story and we have the story item, try to extract media and text from it
                    story_media_parts = []
                    story_text_content = None
                    story_caption_entities = None
                    # Track which source story_text_content came from, and the corresponding raw text and entities
                    story_text_source = None  # 'message_markdown', 'message_text', 'story_markdown', 'story_caption', 'formatted_parts'
                    story_text_raw = None  # The raw text that corresponds to story_text_content (for entity offsets)
                    story_text_entities = None  # The entities that correspond to story_text_raw
                    if is_forwarded_story and story_item:
                        try:
                            # Extract text/caption from the story if available
                            story_caption = getattr(story_item, "caption", None)
                            # StoryItem uses "entities" not "caption_entities" for caption formatting
                            story_caption_entities = getattr(story_item, "entities", None) or getattr(story_item, "caption_entities", None) or []
                            
                            # Check if story_item has text_markdown or text_html like regular messages
                            story_text_markdown = getattr(story_item, "text_markdown", None)
                            story_text_html = getattr(story_item, "text_html", None)
                            
                            # Also check if the original message has text with formatting (sometimes forwarded stories have text in the message itself)
                            message_text_markdown = getattr(message, "text_markdown", None)
                            message_text = getattr(message, "message", None) or getattr(message, "text", None) or ""
                            
                            # Prefer message text_markdown if available (forwarded stories sometimes have formatted text in the message)
                            if message_text_markdown and message_text_markdown.strip() and message_text_markdown != message_text:
                                story_text_content = message_text_markdown
                                story_text_source = 'message_markdown'
                                story_text_raw = message_text
                                story_text_entities = entities  # Use message entities
                            # Then try story text_markdown
                            elif story_text_markdown and story_text_markdown != story_caption:
                                story_text_content = story_text_markdown
                                story_text_source = 'story_markdown'
                                # For story_text_markdown, use story_caption as raw text if available, otherwise we might not have entities
                                story_text_raw = story_caption if story_caption else None
                                story_text_entities = story_caption_entities if story_caption else None
                            # Then try story caption with entities
                            elif story_caption:
                                if story_caption_entities:
                                    story_text_content = _entities_to_markdown(story_caption, story_caption_entities)
                                else:
                                    story_text_content = story_caption
                                story_text_source = 'story_caption'
                                story_text_raw = story_caption
                                story_text_entities = story_caption_entities
                            # Fallback to message text if story has no caption
                            elif message_text and message_text.strip():
                                story_text_content = message_text
                                story_text_source = 'message_text'
                                story_text_raw = message_text
                                story_text_entities = entities  # Use message entities
                            
                            # Extract media from the story item
                            # The story has a 'media' attribute that contains Photo, Document, etc.
                            story_media = getattr(story_item, "media", None)
                            if story_media:
                                # Create a message-like object with the story media
                                # so we can use format_message_for_prompt which handles everything correctly
                                class StoryMessageWrapper:
                                    def __init__(self, media, story_caption=None):
                                        # Map story media types to message attributes
                                        # Story media can be Photo, Document, or wrapped in MessageMediaPhoto/Document
                                        if hasattr(media, "__class__"):
                                            media_class = media.__class__.__name__
                                            if media_class == "MessageMediaPhoto":
                                                self.photo = getattr(media, "photo", None)
                                                self.media = media
                                            elif media_class == "MessageMediaDocument":
                                                self.document = getattr(media, "document", None)
                                                self.media = media
                                            elif media_class == "Photo":
                                                # Direct Photo object
                                                self.photo = media
                                                self.media = media
                                            elif media_class == "Document":
                                                # Direct Document object
                                                self.document = media
                                                self.media = media
                                            else:
                                                # Unknown type, try to set media directly
                                                self.media = media
                                        else:
                                            self.media = media
                                        # Add caption if available
                                        self.text = story_caption if story_caption else None
                                        # Add date for provenance
                                        self.date = getattr(message, "date", None)
                                
                                story_wrapper = StoryMessageWrapper(story_media, story_caption)
                                # Process the story media through the normal pipeline
                                try:
                                    # First inject media descriptions to ensure they're processed
                                    from media.media_injector import inject_media_descriptions
                                    temp_messages = [story_wrapper]
                                    await inject_media_descriptions(temp_messages, agent=agent, peer_id=channel_id)
                                    
                                    # Then format using the standard function which handles everything
                                    story_formatted_parts = await format_message_for_prompt(story_wrapper, agent=agent, media_chain=media_chain)
                                    
                                    # Convert to the format expected by the admin console
                                    for part in story_formatted_parts:
                                        if part.get("kind") == "text" and part.get("text"):
                                            # Skip text parts if we already have story_text_content
                                            if not story_text_content:
                                                story_text_content = part.get("text")
                                                story_text_source = 'formatted_parts'
                                                # Formatted parts might not have corresponding raw text/entities
                                                # Use story_caption if available as fallback
                                                story_text_raw = story_caption if story_caption else None
                                                story_text_entities = story_caption_entities if story_caption else None
                                        elif part.get("kind") == "media":
                                            story_media_parts.append({
                                                "kind": "media",
                                                "media_kind": part.get("media_kind"),
                                                "rendered_text": part.get("rendered_text", ""),
                                                "unique_id": part.get("unique_id"),
                                                "sticker_set_name": part.get("sticker_set_name"),
                                                "sticker_name": part.get("sticker_name"),
                                                "is_animated": part.get("is_animated", False),
                                                "message_id": str(message.id),
                                            })
                                except Exception as e:
                                    logger.debug(f"Failed to process story media in message {message.id}: {e}", exc_info=True)
                        except Exception as e:
                            logger.debug(f"Failed to process story item for message {message.id}: {e}")
                    
                    # Build message parts list (text and media)
                    parts = []
                    for part in message_parts:
                        if part.get("kind") == "text":
                            part_text = part.get("text", "")
                            # Convert markdown to HTML for text parts
                            part_html = markdown_to_html(part_text)
                            # Replace custom emojis with images in text parts
                            # Use raw_text and entities from the original message
                            # Note: part_text is already markdown, so we need the raw text for entity offsets
                            part_html = await _replace_custom_emojis_with_images(
                                part_html, raw_text, entities, agent_config_name, str(message.id), message
                            )
                            parts.append({
                                "kind": "text",
                                "text": part_html
                            })
                        elif part.get("kind") == "media":
                            parts.append({
                                "kind": "media",
                                "media_kind": part.get("media_kind"),
                                "rendered_text": part.get("rendered_text", ""),
                                "unique_id": part.get("unique_id"),
                                "sticker_set_name": part.get("sticker_set_name"),
                                "sticker_name": part.get("sticker_name"),
                                "is_animated": part.get("is_animated", False),  # Include animated flag for stickers
                                "message_id": str(message.id),  # Include message ID for media serving
                            })
                    
                    # Add story text if we extracted any
                    if story_text_content and story_text_content.strip():
                        story_text = story_text_content.strip()
                        # Convert markdown to HTML for story captions
                        story_html = markdown_to_html(story_text)
                        # Replace custom emojis with images in story text
                        # Use the correct raw text and entities based on where story_text_content came from
                        # This ensures UTF-16 offsets in entities match the text being processed
                        story_raw_for_entities = story_text_raw if story_text_raw else story_caption
                        story_entities_for_emoji = story_text_entities if story_text_entities is not None else story_caption_entities
                        # Only process if we have both raw text and entities (entities might be empty list, which is OK)
                        if story_raw_for_entities is not None:
                            story_html = await _replace_custom_emojis_with_images(
                                story_html, story_raw_for_entities, story_entities_for_emoji, agent_config_name, str(message.id), None
                            )
                        # If we don't have raw text, entities won't work anyway, so skip emoji replacement
                        parts.append({
                            "kind": "text",
                            "text": story_html
                        })
                        # For forwarded stories, we've added the story text to parts, so we should clear text
                        # to avoid the frontend potentially rendering both. The frontend will use parts if available.
                        if is_forwarded_story:
                            text = ""  # Clear text since we're using parts instead
                        elif not text:
                            text = story_html  # HTML for display
                    
                    # Add story media parts if we extracted any
                    parts.extend(story_media_parts)
                    
                    # If this is a forwarded story with no parts, add a text part to represent it
                    if is_forwarded_story and not parts:
                        story_text = "Forwarded story"
                        if story_from_name:
                            # Escape story_from_name before inserting into string to prevent XSS
                            # (frontend markdownToHtml will also escape, but defense in depth)
                            story_from_name_escaped = html.escape(story_from_name)
                            story_text = f"Forwarded story from {story_from_name_escaped}"
                        # Convert to HTML (additional escaping for safety)
                        story_html = markdown_to_html(story_text)
                        parts.append({
                            "kind": "text",
                            "text": story_html
                        })
                        # Also update the main text field for consistency
                        if not text:
                            text = story_html
                        logger.debug(f"Added forwarded story text part for message {message.id}: {story_text}")
                    
                    # If text is empty but we have parts, extract text from the first text part
                    # This ensures service messages (which come from format_message_for_prompt) appear in the main text field
                    if not text and parts:
                        for part in parts:
                            if part.get("kind") == "text":
                                part_text = part.get("text", "")
                                if part_text:
                                    text = part_text
                                    break
                    
                    # Also handle case where message has no text and no parts (might be other types of empty messages)
                    # This ensures messages always have at least one part so they appear in the UI
                    if not parts and not text:
                        logger.debug(f"Message {message.id} has no parts and no text - adding placeholder. fwd_from={fwd_from is not None}, is_forwarded_story={is_forwarded_story}")
                        # Check if it's a forwarded message (regular forward, not story)
                        if fwd_from and not is_forwarded_story:
                            # Regular forwarded message with no content - add placeholder
                            parts.append({
                                "kind": "text",
                                "text": "[Forwarded message]"
                            })
                            text = "[Forwarded message]"
                        elif fwd_from:
                            # Has fwd_from but wasn't detected as story - might be a story we didn't detect
                            # Add a generic forwarded story placeholder
                            parts.append({
                                "kind": "text",
                                "text": "[Forwarded story]"
                            })
                            text = "[Forwarded story]"
                        else:
                            # Some other empty message - add placeholder so it appears
                            parts.append({
                                "kind": "text",
                                "text": "[Message]"
                            })
                            text = "[Message]"
                    
                    # Check if message is read by partner (only for DMs and messages sent by agent)
                    is_read_by_partner = None
                    if is_dm_conversation and is_from_agent:
                        # For agent messages in DMs, check if partner has read them
                        # If read_outbox_max_id is None, we can't determine read status
                        if read_outbox_max_id is not None:
                            # Message is read if its ID is <= read_outbox_max_id
                            is_read_by_partner = msg_id <= read_outbox_max_id
                        # If read_outbox_max_id is None, is_read_by_partner remains None
                    
                    messages.append({
                        "id": str(message.id),
                        "text": text,  # HTML-formatted text (XSS-protected via markdown_to_html)
                        "parts": parts,  # Include formatted parts (text + media)
                        "sender_id": str(sender_id) if sender_id else None,
                        "sender_name": sender_name,
                        "is_from_agent": is_from_agent,
                        "timestamp": timestamp,
                        "reply_to_msg_id": reply_to_msg_id,
                        "reactions": reactions_str,
                        "is_read_by_partner": is_read_by_partner,  # True if read, False if unread, None if unknown/not applicable
                    })
                logger.info(
                    f"[{agent_config_name}] Fetched {total_fetched} unsummarized messages for channel {channel_id} "
                    f"(highest_summarized_id={highest_summarized_id}, using min_id filter)"
                )
                return list(reversed(messages))  # Return in chronological order
            except Exception as e:
                logger.error(f"Error fetching messages for {agent_config_name}/{channel_id}: {e}", exc_info=True)
                return []

        # Use agent.execute() to run the coroutines on the agent's event loop
        try:
            messages = agent.execute(_get_messages(), timeout=30.0)
            # Check blocked status
            is_blocked = agent.execute(_check_blocked_status(), timeout=10.0)
            # Get agent timezone identifier (IANA format for JavaScript compatibility)
            agent_tz_id = agent.get_timezone_identifier()
            
            return jsonify({
                "messages": messages,
                "summaries": summaries,
                "agent_timezone": agent_tz_id,
                "is_blocked": is_blocked
            })
        except RuntimeError as e:
            error_msg = str(e).lower()
            if "not authenticated" in error_msg or "not running" in error_msg:
                logger.warning(f"Agent {agent_config_name} client loop issue: {e}")
                return jsonify({"error": "Agent client loop is not available"}), 503
            else:
                logger.error(f"Error fetching conversation: {e}")
                return jsonify({"error": str(e)}), 500
        except TimeoutError:
            logger.warning(f"Timeout fetching conversation for agent {agent_config_name}, user {user_id}")
            return jsonify({"error": "Timeout fetching conversation"}), 504
        except Exception as e:
            logger.error(f"Error fetching conversation: {e}")
            return jsonify({"error": str(e)}), 500
    except Exception as e:
        logger.error(f"Error getting conversation for {agent_config_name}/{user_id}: {e}")
        return jsonify({"error": str(e)}), 500
