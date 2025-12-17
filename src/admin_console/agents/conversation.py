# admin_console/agents/conversation.py
#
# Conversation management routes for the admin console.

import asyncio
import contextlib
import copy
import glob
import html
import json as json_lib
import logging
import os
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from flask import Blueprint, Response, jsonify, request  # pyright: ignore[reportMissingImports]

from admin_console.helpers import get_agent_by_name
from config import CONFIG_DIRECTORIES, STATE_DIRECTORY
from handlers.received_helpers.message_processing import format_message_reactions
from handlers.received import parse_llm_reply
from handlers.received_helpers.summarization import trigger_summarization_directly
from memory_storage import load_property_entries
from media.media_injector import format_message_for_prompt
from media.media_source import MediaStatus, get_default_media_source_chain
from media.media_sources import get_directory_media_source
from media.mime_utils import detect_mime_type_from_bytes, get_file_extension_from_mime_or_bytes, is_tgs_mime_type
from task_graph import WorkQueue
from task_graph_helpers import insert_received_task_for_conversation
from telegram_download import download_media_bytes
from telegram_media import iter_media_parts
from telegram_util import get_channel_name
from telepathic import TELEPATHIC_PREFIXES

logger = logging.getLogger(__name__)

# Safe URL schemes allowed in markdown links (for security)
SAFE_URL_SCHEMES = {'http', 'https', 'mailto', 'tel'}


def _utf16_offset_to_python_index(text: str, utf16_offset: int) -> int:
    """
    Convert a UTF-16 code unit offset to a Python string index.
    
    Telegram uses UTF-16 code units for entity offsets, but Python strings
    use Unicode code points. This function converts between them.
    
    Characters in the Basic Multilingual Plane (U+0000 to U+FFFF) use 1 UTF-16 code unit.
    Characters outside (like many emojis) use 2 UTF-16 code units (surrogate pair).
    
    Args:
        text: The text string
        utf16_offset: Offset in UTF-16 code units
        
    Returns:
        Index in Python string (Unicode code points)
    """
    if utf16_offset == 0:
        return 0
    
    # Count UTF-16 code units character by character
    utf16_count = 0
    for i, char in enumerate(text):
        # Count UTF-16 code units for this character
        # Characters > U+FFFF require a surrogate pair (2 code units)
        utf16_count += 2 if ord(char) > 0xFFFF else 1
        if utf16_count >= utf16_offset:
            return i + 1
    
    # If we've counted all characters and still haven't reached the offset, return the end
    return len(text)


def _entities_to_markdown(text: str, entities: list) -> str:
    """
    Convert Telegram message entities to markdown format.
    
    Args:
        text: Raw message text
        entities: List of MessageEntity objects from Telegram
        
    Returns:
        Markdown-formatted text
    """
    if not text or not entities:
        return text
    
    # Sort entities by offset (position in text), descending, so we can insert markdown without affecting positions
    sorted_entities = sorted(entities, key=lambda e: (getattr(e, "offset", 0), -getattr(e, "length", 0)), reverse=True)
    
    result = text
    for entity in sorted_entities:
        utf16_offset = getattr(entity, "offset", 0)
        utf16_length = getattr(entity, "length", 0)
        entity_type = entity.__class__.__name__ if hasattr(entity, "__class__") else str(type(entity))
        
        # Convert UTF-16 offsets to Python string indices
        # IMPORTANT: Use original 'text' for offset conversion, not 'result'
        # Telegram's entity offsets are based on the original text. As we insert markdown
        # characters into 'result', the string grows, but we need to convert offsets
        # based on the original text, then apply them to the current 'result' state.
        # Since we process entities in reverse order (descending by offset), when we process
        # an entity, all previously processed entities were at higher offsets (later in the
        # original text), so their markdown insertions don't affect the indices for the
        # current entity - we can use the indices directly from the original text conversion.
        start_idx = _utf16_offset_to_python_index(text, utf16_offset)
        end_idx = _utf16_offset_to_python_index(text, utf16_offset + utf16_length)
        
        # Since we process entities in reverse order (descending by offset), previously
        # processed entities are at higher offsets (later in the original text). However,
        # if entities are nested or overlapping, we need to account for markdown inserted
        # by those entities that appears before our positions in the result string.
        current_pos = sorted_entities.index(entity)
        markdown_before_start = 0
        markdown_before_end = 0
        
        # Count markdown inserted before start_idx and before end_idx by previously processed entities
        for prev_entity in sorted_entities[:current_pos]:
            prev_offset = getattr(prev_entity, "offset", 0)
            prev_length = getattr(prev_entity, "length", 0)
            prev_start_idx = _utf16_offset_to_python_index(text, prev_offset)
            prev_end_idx = _utf16_offset_to_python_index(text, prev_offset + prev_length)
            prev_type = prev_entity.__class__.__name__ if hasattr(prev_entity, "__class__") else str(type(prev_entity))
            
            if prev_type == "MessageEntityCustomEmoji":
                continue
            
            # Calculate how much markdown this entity inserted
            opening_len = 0
            closing_len = 0
            if prev_type == "MessageEntityBold":
                opening_len = 2  # "**"
                closing_len = 2  # "**"
            elif prev_type == "MessageEntityItalic":
                opening_len = 2  # "__"
                closing_len = 2  # "__"
            elif prev_type == "MessageEntityCode":
                opening_len = 1  # "`"
                closing_len = 1  # "`"
            elif prev_type in ("MessageEntityTextUrl", "MessageEntityUrl"):
                url = getattr(prev_entity, "url", None) or ""
                if url:
                    opening_len = 1  # "["
                    closing_len = 3 + len(url)  # "]" + "(" + url + ")"
            
            # Count markdown inserted before our start and end positions
            # The opening delimiter is inserted at prev_start_idx
            # The closing delimiter is inserted at prev_end_idx
            # We need to count how much markdown appears before each of our positions in the result string
            
            # Count opening delimiter
            if prev_start_idx <= start_idx:
                # Opening delimiter is at or before our start
                markdown_before_start += opening_len
            elif prev_start_idx < end_idx:
                # Opening delimiter is before our end (but after our start)
                markdown_before_end += opening_len
            
            # Count closing delimiter
            if prev_end_idx <= start_idx:
                # Closing delimiter is at or before our start
                markdown_before_start += closing_len
            elif prev_end_idx <= end_idx:
                # Closing delimiter is before or at our end (but after our start)
                # When prev_end_idx == end_idx, we still count it because we want to insert
                # our closing delimiter after the previous one
                markdown_before_end += closing_len
        
        # Adjust indices to account for markdown inserted before them
        # Note: markdown_before_start shifts both start_idx and end_idx, since it's inserted
        # before start_idx and shifts everything after it. markdown_before_end only shifts end_idx.
        start_idx += markdown_before_start
        end_idx += markdown_before_start + markdown_before_end
        
        # Ensure indices are within bounds
        if start_idx < 0:
            start_idx = 0
        if end_idx < start_idx:
            end_idx = start_idx
        if start_idx > len(result):
            # Skip this entity if start is beyond current result length
            continue
        if end_idx > len(result):
            end_idx = len(result)
        
        # Map entity types to markdown
        # Skip MessageEntityCustomEmoji - these are just metadata about custom emojis,
        # the emoji character itself is already in the text and doesn't need formatting
        if entity_type == "MessageEntityCustomEmoji":
            # Custom emojis don't need markdown formatting - they're already in the text
            continue
        elif entity_type == "MessageEntityBold":
            result = result[:start_idx] + "**" + result[start_idx:end_idx] + "**" + result[end_idx:]
        elif entity_type == "MessageEntityItalic":
            # Use Telegram's native markdown format (__text__) for consistency
            result = result[:start_idx] + "__" + result[start_idx:end_idx] + "__" + result[end_idx:]
        elif entity_type == "MessageEntityCode":
            result = result[:start_idx] + "`" + result[start_idx:end_idx] + "`" + result[end_idx:]
        elif entity_type == "MessageEntityTextUrl" or entity_type == "MessageEntityUrl":
            url = getattr(entity, "url", None) or ""
            if url:
                result = result[:start_idx] + "[" + result[start_idx:end_idx] + "](" + url + ")" + result[end_idx:]
    
    return result


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
            start_idx = _utf16_offset_to_python_index(text, utf16_offset)
            end_idx = _utf16_offset_to_python_index(text, utf16_offset + utf16_length)
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
            start_idx = _utf16_offset_to_python_index(text, utf16_offset)
            end_idx = _utf16_offset_to_python_index(text, utf16_offset + utf16_length)
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
    
    logger.info(f"Admin console: Processing reactions for message {message_id}")
    
    try:
        reactions_obj = getattr(message, 'reactions', None)
        if not reactions_obj:
            logger.info(f"Admin console: Message {message_id} has no reactions object")
            return reactions_str
        
        recent_reactions = getattr(reactions_obj, 'recent_reactions', None)
        if not recent_reactions:
            logger.info(f"Admin console: Message {message_id} has no recent_reactions")
            return reactions_str
        
        logger.info(f"Admin console: Message {message_id} has {len(recent_reactions)} reaction(s)")
        
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


def _is_safe_url(url: str) -> bool:
    """
    Check if a URL uses a safe protocol.
    
    Only allows http, https, mailto, tel, and relative URLs (starting with / or #).
    Rejects javascript:, data:, vbscript:, protocol-relative URLs (starting with //),
    and other dangerous protocols.
    
    Args:
        url: The URL to validate
        
    Returns:
        True if the URL is safe, False otherwise
    """
    if not url:
        return False
    
    # Reject protocol-relative URLs (starting with //) - these resolve to external domains
    # using the current page's protocol, enabling open redirect attacks
    if url.startswith('//'):
        return False
    
    # Allow relative URLs (starting with / or #)
    if url.startswith('/') or url.startswith('#'):
        return True
    
    # Parse the URL to get the scheme
    try:
        parsed = urlparse(url)
        scheme = parsed.scheme.lower()
        
        # Only allow safe protocols
        return scheme in SAFE_URL_SCHEMES
    except Exception:
        # If parsing fails, reject the URL
        return False


def markdown_to_html(text: str) -> str:
    """
    Convert Telegram markdown formatting to HTML for frontend display.
    
    Converts Telegram markdown patterns to safe HTML:
    - `**bold**` → `<strong>`
    - `__italic__` → `<em>`
    - `` `code` `` → `<code>` (single backtick before and after)
    - `[text](url)` → `<a href="url">text</a>` (only for safe URLs)
    
    Security: This function escapes HTML characters and validates URLs to prevent XSS attacks.
    
    Args:
        text: Markdown-formatted text (from Telegram's text_markdown property)
        
    Returns:
        HTML-formatted text safe for frontend rendering
    """
    if not text:
        return ""
    
    # Use placeholders to protect links during processing
    # This allows us to process links on raw text (to extract/validate URLs),
    # then escape everything, then restore the links
    # Use a format that won't conflict with markdown (no underscores, asterisks, or backticks)
    link_placeholders = {}
    placeholder_counter = [0]  # Use list to allow modification in nested function
    
    def replace_link_with_placeholder(match):
        link_text = match.group(1)
        url = match.group(2)
        placeholder = f"LINKPLACEHOLDER{placeholder_counter[0]}LINKPLACEHOLDER"
        placeholder_counter[0] += 1
        if _is_safe_url(url):
            # URL is safe, store the link HTML (with properly escaped URL and text)
            link_placeholders[placeholder] = f'<a href="{html.escape(url)}">{html.escape(link_text)}</a>'
        else:
            # URL is not safe, store just the escaped text
            link_placeholders[placeholder] = html.escape(link_text)
        return placeholder
    
    # Step 1: Process links first, replace with placeholders
    text_with_placeholders = re.sub(r'\[([^\]]+?)\]\(([^)]+?)\)', replace_link_with_placeholder, text)
    
    # Step 2: Escape all HTML characters (this escapes user-provided HTML but not our placeholders)
    escaped_text = html.escape(text_with_placeholders)
    
    # Step 3: Process Telegram markdown patterns on escaped text
    # Note: In Telegram markdown, **text** is bold and __text__ is italic
    html_output = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', escaped_text, flags=re.DOTALL)
    html_output = re.sub(r'__(.+?)__', r'<em>\1</em>', html_output, flags=re.DOTALL)
    html_output = re.sub(r'`([^`]+?)`', r'<code>\1</code>', html_output)
    
    # Step 4: Restore link placeholders with actual link HTML
    # The placeholders were escaped, so we need to unescape them first
    for placeholder, link_html in link_placeholders.items():
        # The placeholder was escaped, so replace the escaped version
        escaped_placeholder = html.escape(placeholder)
        html_output = html_output.replace(escaped_placeholder, link_html)
    
    return html_output

# Translation JSON schema for message translation
_TRANSLATION_SCHEMA = {
    "type": "object",
    "properties": {
        "translations": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "message_id": {
                        "type": "string",
                        "description": "The message ID from the input"
                    },
                    "translated_text": {
                        "type": "string",
                        "description": "The English translation of the message text, preserving HTML formatting (bold, italic, links, etc.)"
                    }
                },
                "required": ["message_id", "translated_text"],
                "additionalProperties": False
            }
        }
    },
    "required": ["translations"],
    "additionalProperties": False
}


def _get_highest_summarized_message_id_for_api(agent_config_name: str, channel_id: int) -> int | None:
    """
    Get the highest message ID that has been summarized (for use in Flask context).
    
    Everything with message ID <= this value can be assumed to be summarized.
    Returns None if no summaries exist.
    """
    try:
        summary_file = Path(STATE_DIRECTORY) / agent_config_name / "memory" / f"{channel_id}.json"
        summaries, _ = load_property_entries(summary_file, "summary", default_id_prefix="summary")
        
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


def _has_conversation_content_local(agent_config_name: str, channel_id: int) -> bool:
    """
    Check if a conversation has content by checking local files only (no Telegram API calls).
    
    Returns True if summaries exist or if the summary file exists (indicating conversation data).
    """
    try:
        summary_file = Path(STATE_DIRECTORY) / agent_config_name / "memory" / f"{channel_id}.json"
        if not summary_file.exists():
            return False
        
        summaries, _ = load_property_entries(summary_file, "summary", default_id_prefix="summary")
        # If summaries exist, there's conversation content
        return len(summaries) > 0
    except Exception:
        return False


def register_conversation_routes(agents_bp: Blueprint):
    """Register conversation management routes."""
    
    @agents_bp.route("/api/agents/<agent_config_name>/conversation-content-check", methods=["POST"])
    def api_check_conversation_content_batch(agent_config_name: str):
        """
        Batch check which partners have conversation content (local files only, no Telegram API calls).
        
        Request body: {"user_ids": ["user_id1", "user_id2", ...]}
        Response: {"content_checks": {"user_id1": true, "user_id2": false, ...}}
        """
        try:
            agent = get_agent_by_name(agent_config_name)
            if not agent:
                return jsonify({"error": f"Agent '{agent_config_name}' not found"}), 404

            data = request.json or {}
            user_ids = data.get("user_ids", [])
            
            if not isinstance(user_ids, list):
                return jsonify({"error": "user_ids must be a list"}), 400

            content_checks = {}
            for user_id_str in user_ids:
                try:
                    channel_id = int(user_id_str)
                    content_checks[user_id_str] = _has_conversation_content_local(agent.config_name, channel_id)
                except (ValueError, TypeError):
                    content_checks[user_id_str] = False

            return jsonify({"content_checks": content_checks})
        except Exception as e:
            logger.error(f"Error checking conversation content for {agent_config_name}: {e}")
            return jsonify({"error": str(e)}), 500

    @agents_bp.route("/api/agents/<agent_config_name>/conversation/<user_id>", methods=["GET"])
    def api_get_conversation(agent_config_name: str, user_id: str):
        """Get conversation history (unsummarized messages only) and summaries."""
        try:
            agent = get_agent_by_name(agent_config_name)
            if not agent:
                return jsonify({"error": f"Agent '{agent_config_name}' not found"}), 404

            if not agent.client or not agent.client.is_connected():
                return jsonify({"error": "Agent client not connected"}), 503

            try:
                channel_id = int(user_id)
            except ValueError:
                return jsonify({"error": "Invalid user ID"}), 400

            # Get summaries
            summary_file = Path(STATE_DIRECTORY) / agent.config_name / "memory" / f"{channel_id}.json"
            summaries, _ = load_property_entries(summary_file, "summary", default_id_prefix="summary")
            summaries.sort(key=lambda x: (x.get("min_message_id", 0), x.get("max_message_id", 0)))
            
            # Trigger backfill for missing dates using agent's executor (runs in agent's thread)
            try:
                async def _backfill_dates():
                    try:
                        storage = agent._storage
                        if storage:
                            await storage.backfill_summary_dates(channel_id, agent)
                    except Exception as e:
                        logger.warning(f"Backfill failed for {agent_config_name}/{user_id}: {e}", exc_info=True)
                
                # Schedule backfill in agent's thread (non-blocking, fire-and-forget)
                executor = agent.executor
                if executor and executor.loop and executor.loop.is_running():
                    # Schedule the coroutine without waiting for it
                    asyncio.run_coroutine_threadsafe(_backfill_dates(), executor.loop)
                    logger.info(f"Scheduled backfill for {agent_config_name}/{user_id} (channel {channel_id})")
                else:
                    logger.info(
                        f"Agent executor not available for {agent_config_name}, skipping backfill. "
                        f"executor={executor}, loop={executor.loop if executor else None}, "
                        f"is_running={executor.loop.is_running() if executor and executor.loop else None}"
                    )
            except Exception as e:
                # Don't fail the request if backfill setup fails
                logger.warning(f"Failed to setup backfill for {agent_config_name}/{user_id}: {e}", exc_info=True)
            
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
            
            async def _get_messages():
                try:
                    # Use client.get_entity() directly since we're already in the client's event loop
                    # This avoids event loop mismatch issues with agent.get_cached_entity()
                    client = agent.client
                    entity = await client.get_entity(channel_id)
                    if not entity:
                        return []
                    
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
                                        # Import here to avoid issues if stories API is not available
                                        from telethon.tl.functions.stories import GetStoriesByIDRequest  # pyright: ignore[reportMissingImports]
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
                                    "text": part_html  # HTML for display
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
                                "text": story_html  # HTML for display
                            })
                            # For forwarded stories, we've added the story text to parts, so we should clear text
                            # to avoid the frontend potentially rendering both. The frontend will use parts if available.
                            if is_forwarded_story:
                                text = ""  # Clear text since we're using parts instead
                            elif not text:
                                text = story_text
                        
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
                                "text": story_html  # HTML for display
                            })
                            # Also update the main text field for consistency
                            if not text:
                                text = story_html
                            logger.debug(f"Added forwarded story text part for message {message.id}: {story_text}")
                        
                        # Also handle case where message has no text and no parts (might be other types of empty messages)
                        # This ensures messages always have at least one part so they appear in the UI
                        if not parts and not text:
                            logger.debug(f"Message {message.id} has no parts and no text - adding placeholder. fwd_from={fwd_from is not None}, is_forwarded_story={is_forwarded_story}")
                            # Check if it's a forwarded message (regular forward, not story)
                            if fwd_from and not is_forwarded_story:
                                # Regular forwarded message with no content - add placeholder
                                placeholder_text = "[Forwarded message]"
                                placeholder_html = html.escape(placeholder_text)
                                parts.append({
                                    "kind": "text",
                                    "text": placeholder_html  # HTML for display
                                })
                                text = placeholder_html
                            elif fwd_from:
                                # Has fwd_from but wasn't detected as story - might be a story we didn't detect
                                # Add a generic forwarded story placeholder
                                placeholder_text = "[Forwarded story]"
                                placeholder_html = html.escape(placeholder_text)
                                parts.append({
                                    "kind": "text",
                                    "text": placeholder_html  # HTML for display
                                })
                                text = placeholder_html
                            else:
                                # Some other empty message - add placeholder so it appears
                                placeholder_text = "[Message]"
                                placeholder_html = html.escape(placeholder_text)
                                parts.append({
                                    "kind": "text",
                                    "text": placeholder_html  # HTML for display
                                })
                                text = placeholder_html
                        
                        messages.append({
                            "id": str(message.id),
                            "text": text,  # HTML for display
                            "parts": parts,  # Include formatted parts (text + media), all in HTML
                            "sender_id": str(sender_id) if sender_id else None,
                            "sender_name": sender_name,
                            "is_from_agent": is_from_agent,
                            "timestamp": timestamp,
                            "reply_to_msg_id": reply_to_msg_id,
                            "reactions": reactions_str,
                        })
                    logger.info(
                        f"[{agent_config_name}] Fetched {total_fetched} unsummarized messages for channel {channel_id} "
                        f"(highest_summarized_id={highest_summarized_id}, using min_id filter)"
                    )
                    return list(reversed(messages))  # Return in chronological order
                except Exception as e:
                    logger.error(f"Error fetching messages for {agent_config_name}/{channel_id}: {e}", exc_info=True)
                    return []

            # Use agent.execute() to run the coroutine on the agent's event loop
            try:
                messages = agent.execute(_get_messages(), timeout=30.0)
                # Get agent timezone (property handles fallback to server timezone)
                agent_tz = agent.timezone
                
                return jsonify({
                    "messages": messages,
                    "summaries": summaries,
                    "agent_timezone": str(agent_tz)
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

    @agents_bp.route("/api/agents/<agent_config_name>/conversation/<user_id>/translate", methods=["POST"])
    def api_translate_conversation(agent_config_name: str, user_id: str):
        """Translate unsummarized messages into English using the media LLM."""
        try:
            agent = get_agent_by_name(agent_config_name)
            if not agent:
                return jsonify({"error": f"Agent '{agent_config_name}' not found"}), 404

            try:
                channel_id = int(user_id)
            except ValueError:
                return jsonify({"error": "Invalid user ID"}), 400

            # Get messages from request
            data = request.json
            messages = data.get("messages", [])
            if not messages:
                return jsonify({"error": "No messages provided"}), 400

            # Check if agent's event loop is accessible
            try:
                client_loop = agent._get_client_loop()
                if not client_loop or not client_loop.is_running():
                    raise RuntimeError("Agent client event loop is not accessible or not running")
            except Exception as e:
                logger.warning(f"Cannot translate conversation - event loop check failed: {e}")
                return jsonify({"error": "Agent client event loop is not available"}), 503

            # Use the agent's LLM for translation
            agent_llm = agent.llm

            # Build translation prompt with messages as structured JSON
            # Send HTML to LLM and ask it to preserve HTML formatting in translations
            messages_for_prompt = []
            for msg in messages:
                msg_id = msg.get("id", "")
                # Extract HTML from parts first (preferred), then fall back to main text field
                msg_html = ""
                parts = msg.get("parts", [])
                if parts:
                    # Collect all text parts (already HTML)
                    text_parts = []
                    for part in parts:
                        if part.get("kind") == "text":
                            part_html = part.get("text", "")
                            if part_html:
                                text_parts.append(part_html)
                    if text_parts:
                        msg_html = " ".join(text_parts)
                
                # Fall back to main text field if no parts or no text in parts
                if not msg_html:
                    msg_html = msg.get("text", "")
                
                if msg_html:
                    messages_for_prompt.append({
                        "message_id": str(msg_id),
                        "text": msg_html
                    })
            
            # Convert to JSON string for the prompt (properly escaped)
            import json as json_module
            messages_json = json_module.dumps(messages_for_prompt, ensure_ascii=False, indent=2)
            
            translation_prompt = (
                "Translate the conversation messages into English.\n"
                "The messages contain HTML formatting (bold, italic, links, etc.). "
                "IMPORTANT: Preserve all HTML formatting in your translations. "
                "If the original has <strong>bold</strong> text, the translation should also have <strong>bold</strong> text. "
                "If the original has <em>italic</em> text, the translation should also have <em>italic</em> text. "
                "Preserve <a> links, <code> code blocks, and all other HTML tags.\n"
                "\n"
                "Return a JSON object with this structure:\n"
                "{\n"
                "  \"translations\": [\n"
                "    {\"message_id\": \"123\", \"translated_text\": \"English translation with <strong>HTML</strong> preserved\"},\n"
                "    ...\n"
                "  ]\n"
                "}\n"
                "\n"
                "Translate all messages provided, maintaining the order and message IDs. "
                "Ensure all JSON is properly formatted and preserves HTML formatting.\n"
                "\n"
                "Input messages (as JSON, with HTML formatting):\n"
                f"{messages_json}\n"
            )

            # This is async, so we need to run it in the client's event loop
            async def _translate_messages():
                try:
                    # Use the shared query_with_json_schema API for LLM-agnostic translation
                    system_prompt = (
                        "You are a translation assistant. Translate messages into English and return JSON.\n\n"
                        f"{translation_prompt}"
                    )
                    
                    result_text = await agent_llm.query_with_json_schema(
                        system_prompt=system_prompt,
                        json_schema=copy.deepcopy(_TRANSLATION_SCHEMA),
                        model=None,  # Use default model
                        timeout_s=None,  # Use default timeout
                    )
                    
                    if result_text:
                        # Parse JSON response with better error handling
                        try:
                            result = json_lib.loads(result_text)
                            translations = result.get("translations", [])
                            if isinstance(translations, list):
                                return translations
                            else:
                                logger.warning(f"Translations is not a list: {type(translations)}")
                                return []
                        except json_lib.JSONDecodeError as e:
                            logger.error(f"JSON decode error in translation response: {e}")
                            logger.debug(f"Response text length: {len(result_text)} chars")
                            logger.debug(f"Response text (first 1000 chars): {result_text[:1000]}")
                            logger.debug(f"Response text (last 1000 chars): {result_text[-1000:]}")
                            
                            # Check if response appears truncated (common with long conversations)
                            if "Unterminated" in str(e) or "Expecting" in str(e):
                                logger.warning(f"Translation response appears truncated. Response length: {len(result_text)} chars. This may indicate the conversation is too long for a single translation.")
                                # Try to extract partial translations from what we have
                                # Look for complete translation entries before the truncation
                                # Try to find all complete translation entries
                                translation_pattern = r'\{"message_id":\s*"([^"]+)",\s*"translated_text":\s*"([^"]*)"\}'
                                matches = re.findall(translation_pattern, result_text)
                                if matches:
                                    partial_translations = [{"message_id": mid, "translated_text": text} for mid, text in matches]
                                    logger.info(f"Extracted {len(partial_translations)} partial translations from truncated response")
                                    return partial_translations
                            
                            # Try to extract JSON from markdown code blocks if present
                            json_match = re.search(r'```(?:json)?\s*(\{.*\})\s*```', result_text, re.DOTALL)
                            if json_match:
                                try:
                                    result = json_lib.loads(json_match.group(1))
                                    return result.get("translations", [])
                                except json_lib.JSONDecodeError:
                                    pass
                            # Try to find JSON object in the text (more lenient)
                            json_match = re.search(r'\{[^{}]*"translations"[^{}]*\[.*?\]\s*\}', result_text, re.DOTALL)
                            if json_match:
                                try:
                                    result = json_lib.loads(json_match.group(0))
                                    return result.get("translations", [])
                                except json_lib.JSONDecodeError:
                                    pass
                            
                            logger.error(f"Failed to parse translation response. Returning empty translations.")
                            return []
                    
                    return []
                except Exception as e:
                    logger.error(f"Error translating messages: {e}")
                    return []

            # Use agent.execute() to run the coroutine on the agent's event loop
            try:
                translations = agent.execute(_translate_messages(), timeout=60.0)
                
                # Convert to dict for easy lookup
                # Translation text comes from LLM as HTML (preserves formatting)
                # No escaping needed - HTML is already properly formatted
                translation_dict = {
                    t["message_id"]: t["translated_text"]
                    for t in translations
                }
                
                return jsonify({"translations": translation_dict})
            except RuntimeError as e:
                error_msg = str(e).lower()
                if "not authenticated" in error_msg or "not running" in error_msg:
                    logger.warning(f"Agent {agent_config_name} client loop issue: {e}")
                    return jsonify({"error": "Agent client loop is not available"}), 503
                else:
                    logger.error(f"Error translating conversation: {e}")
                    return jsonify({"error": str(e)}), 500
            except TimeoutError:
                logger.warning(f"Timeout translating conversation for agent {agent_config_name}, user {user_id}")
                return jsonify({"error": "Timeout translating conversation"}), 504
            except Exception as e:
                logger.error(f"Error translating conversation: {e}")
                return jsonify({"error": str(e)}), 500
        except Exception as e:
            logger.error(f"Error translating conversation for {agent_config_name}/{user_id}: {e}")
            return jsonify({"error": str(e)}), 500

    @agents_bp.route("/api/agents/<agent_config_name>/xsend/<user_id>", methods=["POST"])
    def api_xsend(agent_config_name: str, user_id: str):
        """Create an xsend task to trigger a received task on another channel."""
        try:
            agent = get_agent_by_name(agent_config_name)
            if not agent:
                return jsonify({"error": f"Agent '{agent_config_name}' not found"}), 404

            if not agent.agent_id:
                return jsonify({"error": "Agent not authenticated"}), 400

            try:
                channel_id = int(user_id)
            except ValueError:
                return jsonify({"error": "Invalid user ID"}), 400

            data = request.json
            intent = data.get("intent", "").strip()

            # Get work queue singleton
            state_path = os.path.join(STATE_DIRECTORY, "work_queue.json")
            work_queue = WorkQueue.get_instance()

            # Create xsend task by inserting a received task with xsend_intent
            # This is async, so we need to run it on the agent's event loop
            async def _create_xsend():
                await insert_received_task_for_conversation(
                    recipient_id=agent.agent_id,
                    channel_id=str(channel_id),
                    xsend_intent=intent if intent else None,
                )
                # Save work queue back to state file
                work_queue.save(state_path)

            # Use agent.execute() to run the coroutine on the agent's event loop
            try:
                agent.execute(_create_xsend(), timeout=30.0)
                return jsonify({"success": True, "message": "XSend task created successfully"})
            except RuntimeError as e:
                error_msg = str(e).lower()
                if "not authenticated" in error_msg or "not running" in error_msg:
                    logger.warning(f"Agent {agent_config_name} client loop issue: {e}")
                    return jsonify({"error": "Agent client loop is not available"}), 503
                else:
                    logger.error(f"Error creating xsend task: {e}")
                    return jsonify({"error": str(e)}), 500
            except TimeoutError:
                logger.warning(f"Timeout creating xsend task for agent {agent_config_name}, user {user_id}")
                return jsonify({"error": "Timeout creating xsend task"}), 504
        except Exception as e:
            logger.error(f"Error creating xsend task for {agent_config_name}/{user_id}: {e}")
            return jsonify({"error": str(e)}), 500

    @agents_bp.route("/api/agents/<agent_config_name>/emoji/<document_id>", methods=["GET"])
    def api_get_custom_emoji(agent_config_name: str, document_id: str):
        """Serve custom emoji image by document ID, using media pipeline for caching and downloading."""
        try:
            agent = get_agent_by_name(agent_config_name)
            if not agent:
                return jsonify({"error": f"Agent '{agent_config_name}' not found"}), 404

            try:
                doc_id = int(document_id)
            except ValueError:
                return jsonify({"error": "Invalid document ID"}), 400

            async def _get_emoji():
                try:
                    # Use GetCustomEmojiDocumentsRequest to fetch the document by document_id
                    from telethon.tl.functions.messages import GetCustomEmojiDocumentsRequest  # pyright: ignore[reportMissingImports]
                    
                    logger.debug(f"Fetching custom emoji document {doc_id} using GetCustomEmojiDocumentsRequest")
                    # Fetch the custom emoji document
                    result = await agent.client(GetCustomEmojiDocumentsRequest(document_id=[doc_id]))
                    
                    if not result:
                        logger.warning(f"Custom emoji document {doc_id} - GetCustomEmojiDocumentsRequest returned None")
                        return None, None
                    
                    # Check different possible result structures
                    documents = None
                    if hasattr(result, "documents"):
                        documents = result.documents
                    elif hasattr(result, "document"):
                        documents = [result.document] if result.document else []
                    elif isinstance(result, list):
                        documents = result
                    
                    if not documents or len(documents) == 0:
                        logger.warning(f"Custom emoji document {doc_id} not found via GetCustomEmojiDocumentsRequest")
                        return None, None
                    
                    # Get the first document (should only be one for a single document_id)
                    doc = documents[0] if documents else None
                    if not doc:
                        logger.warning(f"Custom emoji document {doc_id} returned empty result")
                        return None, None
                    
                    # Get unique_id from document for use with media pipeline
                    from telegram_media import get_unique_id
                    unique_id = get_unique_id(doc)
                    if not unique_id:
                        logger.warning(f"Custom emoji document {doc_id} has no unique_id")
                        return None, None
                    
                    logger.info(f"Custom emoji: document_id={doc_id}, unique_id={unique_id}")
                    
                    # Extract sticker set information from document attributes
                    sticker_set_name = None
                    sticker_set_id = None
                    sticker_access_hash = None
                    sticker_name = None
                    
                    attrs = getattr(doc, "attributes", None)
                    if isinstance(attrs, (list, tuple)):
                        for a in attrs:
                            # Check for DocumentAttributeSticker (regular sticker) or DocumentAttributeCustomEmoji
                            if hasattr(a, "stickerset"):
                                ss = getattr(a, "stickerset", None)
                                if ss:
                                    sticker_set_name = getattr(ss, "short_name", None)
                                    sticker_set_id = getattr(ss, "id", None)
                                    sticker_access_hash = getattr(ss, "access_hash", None)
                                # Get sticker name (emoji character)
                                sticker_name = getattr(a, "alt", None)
                    
                    # Also check emoji directly on document
                    if not sticker_name:
                        sticker_name = getattr(doc, "emoji", None)
                    
                    # If we have sticker_set_id but no short_name, query the set to get the name, title, and emoji status
                    sticker_set_title = None
                    is_emoji_set = None
                    
                    if sticker_set_id and not sticker_set_name:
                        try:
                            from telethon.tl.functions.messages import GetStickerSetRequest
                            from telethon.tl.types import InputStickerSetID
                            
                            logger.debug(f"Querying sticker set for custom emoji {doc_id}: set_id={sticker_set_id}")
                            
                            sticker_set_result = await agent.client(
                                GetStickerSetRequest(
                                    stickerset=InputStickerSetID(
                                        id=sticker_set_id,
                                        access_hash=sticker_access_hash or 0
                                    ),
                                    hash=0
                                )
                            )
                            
                            if sticker_set_result and hasattr(sticker_set_result, 'set'):
                                set_obj = sticker_set_result.set
                                sticker_set_name = getattr(set_obj, 'short_name', None)
                                sticker_set_title = getattr(set_obj, 'title', None)
                                
                                # Check if this is an emoji set
                                if hasattr(set_obj, 'emojis') and getattr(set_obj, 'emojis', False):
                                    is_emoji_set = True
                                else:
                                    # Check set_type attribute if available
                                    set_type = getattr(set_obj, 'set_type', None)
                                    if set_type:
                                        type_str = str(set_type)
                                        if 'emoji' in type_str.lower() or 'Emoji' in type_str:
                                            is_emoji_set = True
                                
                                if sticker_set_name:
                                    logger.debug(f"Got sticker set info for custom emoji {doc_id}: name={sticker_set_name}, title={sticker_set_title}, is_emoji_set={is_emoji_set}")
                        except Exception as e:
                            logger.debug(f"Failed to query sticker set for custom emoji {doc_id}: {e}")
                    
                    # Use media pipeline to get/cache the emoji
                    # This will handle caching, downloading, and description generation
                    media_chain = get_default_media_source_chain()
                    
                    logger.info(f"Calling media pipeline for custom emoji {doc_id}: unique_id={unique_id}, sticker_set={sticker_set_name}, is_emoji_set={is_emoji_set}, sticker_name={sticker_name}")
                    
                    # Build metadata dict to pass additional fields
                    metadata = {}
                    if sticker_set_title is not None:
                        metadata['sticker_set_title'] = sticker_set_title
                    if is_emoji_set is not None:
                        metadata['is_emoji_set'] = is_emoji_set
                    
                    record = await media_chain.get(
                        unique_id=unique_id,
                        agent=agent,
                        doc=doc,
                        kind="sticker",  # Custom emojis are treated as stickers
                        sender_id=None,
                        sender_name=None,
                        channel_id=None,
                        channel_name=None,
                        sticker_set_name=sticker_set_name,
                        sticker_set_id=sticker_set_id,
                        sticker_access_hash=sticker_access_hash,
                        sticker_name=sticker_name,
                        **metadata  # Pass additional metadata fields
                    )
                    
                    if not record:
                        logger.warning(f"Custom emoji {doc_id} (unique_id: {unique_id}) not found via media pipeline")
                        return None, None
                    
                    logger.info(f"Media pipeline returned record for custom emoji {doc_id}: status={record.get('status')}, description={record.get('description')[:50] if record.get('description') else None}")
                    
                    # After calling media_chain.get(), the file should be cached
                    # Find the cached file using unique_id
                    import glob
                    cached_file = None
                    escaped_unique_id = glob.escape(unique_id)
                    
                    # Check all config directories first (curated media)
                    for config_dir in CONFIG_DIRECTORIES:
                        config_media_dir = Path(config_dir) / "media"
                        if config_media_dir.exists() and config_media_dir.is_dir():
                            for file_path in config_media_dir.glob(f"{escaped_unique_id}.*"):
                                if file_path.suffix.lower() != ".json":
                                    cached_file = file_path
                                    break
                            if cached_file:
                                break
                    
                    # If not found in config directories, check state/media/
                    if not cached_file:
                        state_media_dir = Path(STATE_DIRECTORY) / "media"
                        if state_media_dir.exists() and state_media_dir.is_dir():
                            for file_path in state_media_dir.glob(f"{escaped_unique_id}.*"):
                                if file_path.suffix.lower() != ".json":
                                    cached_file = file_path
                                    break
                    
                    if not cached_file or not cached_file.exists():
                        logger.warning(f"Custom emoji {doc_id} (unique_id: {unique_id}) processed but cached file not found")
                        return None, None
                    
                    # Read the cached file
                    with open(cached_file, "rb") as f:
                        emoji_bytes = f.read()
                    
                    return emoji_bytes, unique_id
                except Exception as e:
                    logger.error(f"Error fetching custom emoji {doc_id}: {e}", exc_info=True)
                    return None, None

            # Use agent.execute() to run the coroutine on the agent's event loop
            try:
                emoji_bytes, unique_id = agent.execute(_get_emoji(), timeout=10.0)
                if not emoji_bytes:
                    logger.warning(f"Custom emoji {document_id} not found or failed to download")
                    return jsonify({"error": "Emoji not found"}), 404
                
                # Detect MIME type
                mime_type = detect_mime_type_from_bytes(emoji_bytes)
                if not mime_type:
                    mime_type = "image/webp"  # Default for custom emojis
                
                # Check if it's an animated emoji (TGS/Lottie)
                is_animated = is_tgs_mime_type(mime_type)
                
                headers = {
                    "Cache-Control": "public, max-age=86400",  # Cache for 1 day
                }
                if is_animated:
                    headers["X-Emoji-Type"] = "animated"  # Signal to frontend that this needs Lottie
                
                return Response(
                    emoji_bytes,
                    mimetype=mime_type,
                    headers=headers
                )
            except RuntimeError as e:
                error_msg = str(e).lower()
                if "not authenticated" in error_msg or "not running" in error_msg:
                    logger.warning(f"Agent {agent_config_name} client loop issue: {e}")
                    return jsonify({"error": "Agent client loop is not available"}), 503
                else:
                    logger.error(f"Error fetching custom emoji: {e}")
                    return jsonify({"error": str(e)}), 500
            except TimeoutError:
                logger.warning(f"Timeout fetching custom emoji for agent {agent_config_name}, document {document_id}")
                return jsonify({"error": "Timeout fetching emoji"}), 504
            except Exception as e:
                logger.error(f"Error fetching custom emoji: {e}")
                return jsonify({"error": str(e)}), 500
        except Exception as e:
            logger.error(f"Error getting custom emoji for {agent_config_name}/{document_id}: {e}")
            return jsonify({"error": str(e)}), 500

    @agents_bp.route("/api/agents/<agent_config_name>/conversation/<user_id>/media/<message_id>/<unique_id>", methods=["GET"])
    def api_get_conversation_media(agent_config_name: str, user_id: str, message_id: str, unique_id: str):
        """Serve media from a Telegram message, using cache if available."""
        try:
            agent = get_agent_by_name(agent_config_name)
            if not agent:
                return jsonify({"error": f"Agent '{agent_config_name}' not found"}), 404

            try:
                channel_id = int(user_id)
                msg_id = int(message_id)
            except ValueError:
                return jsonify({"error": "Invalid user ID or message ID"}), 400

            # First, check if media is cached in any of the media directories
            # Check config directories first (curated media), then state/media/ (AI cache)
            # This matches the priority order of the media source chain
            cached_file = None
            
            # Escape unique_id to prevent glob pattern injection attacks
            escaped_unique_id = glob.escape(unique_id)
            
            # Check all config directories first (without fallback to state/media/)
            for config_dir in CONFIG_DIRECTORIES:
                config_media_dir = Path(config_dir) / "media"
                if config_media_dir.exists() and config_media_dir.is_dir():
                    # Search only in this config directory (no fallback)
                    for file_path in config_media_dir.glob(f"{escaped_unique_id}.*"):
                        if file_path.suffix.lower() != ".json":
                            cached_file = file_path
                            break
                    if cached_file:
                        break
            
            # If not found in any config directory, check state/media/ directly
            if not cached_file:
                state_media_dir = Path(STATE_DIRECTORY) / "media"
                if state_media_dir.exists() and state_media_dir.is_dir():
                    for file_path in state_media_dir.glob(f"{escaped_unique_id}.*"):
                        if file_path.suffix.lower() != ".json":
                            cached_file = file_path
                            break
            
            # If found in cache, serve from cache
            if cached_file and cached_file.exists():
                try:
                    # Read the cached file
                    with open(cached_file, "rb") as f:
                        media_bytes = f.read()
                    
                    # Detect MIME type
                    mime_type = detect_mime_type_from_bytes(media_bytes[:1024])
                    
                    logger.debug(
                        f"Serving cached media {unique_id} from {cached_file} for {agent_config_name}/{user_id}/{message_id}"
                    )
                    
                    return Response(
                        media_bytes,
                        mimetype=mime_type or "application/octet-stream",
                        headers={"Content-Disposition": f"inline; filename={unique_id}"}
                    )
                except Exception as e:
                    logger.warning(f"Error reading cached media file {cached_file}: {e}, falling back to Telegram download")
                    # Fall through to download from Telegram
            
            # Not in cache, or cache read failed - download from Telegram
            if not agent.client or not agent.client.is_connected():
                return jsonify({"error": "Agent client not connected"}), 503

            # Check if agent's event loop is accessible
            try:
                client_loop = agent._get_client_loop()
                if not client_loop or not client_loop.is_running():
                    raise RuntimeError("Agent client event loop is not accessible or not running")
            except Exception as e:
                logger.warning(f"Cannot fetch media - event loop check failed: {e}")
                return jsonify({"error": "Agent client event loop is not available"}), 503
            
            # This is async, so we need to run it in the client's event loop
            async def _get_media():
                try:
                    client = agent.client
                    entity = await client.get_entity(channel_id)
                    
                    # Get the message
                    message = await client.get_messages(entity, ids=msg_id)
                    if not message:
                        return None, None, None
                    
                    # Handle case where get_messages returns a list
                    if isinstance(message, list):
                        if len(message) == 0:
                            return None, None, None
                        message = message[0]
                    
                    # Find the media item with matching unique_id
                    media_items = iter_media_parts(message)
                    for item in media_items:
                        if item.unique_id == unique_id:
                            # Download media bytes
                            media_bytes = await download_media_bytes(client, item.file_ref)
                            # Detect MIME type
                            mime_type = detect_mime_type_from_bytes(media_bytes[:1024])
                            return media_bytes, mime_type, item
                    
                    return None, None, None
                except Exception as e:
                    logger.error(f"Error fetching media: {e}")
                    return None, None, None

            # Use agent.execute() to run the coroutine on the agent's event loop
            try:
                media_bytes, mime_type, media_item = agent.execute(_get_media(), timeout=30.0)
                if media_bytes is None:
                    return jsonify({"error": "Media not found"}), 404
                
                logger.debug(
                    f"Downloaded media {unique_id} from Telegram for {agent_config_name}/{user_id}/{message_id}"
                )
                
                # Cache the downloaded media file to state/media/ for future use
                # Use the same storage mechanism as the normal media source chain
                try:
                    # Get file extension from MIME type or by detecting from bytes
                    file_extension = get_file_extension_from_mime_or_bytes(mime_type, media_bytes)
                    
                    # Store media file if we have an extension
                    if file_extension:
                        # Get the shared DirectoryMediaSource instance for state/media/
                        state_media_dir = Path(STATE_DIRECTORY) / "media"
                        cache_source = get_directory_media_source(state_media_dir)
                        
                        # Check if file already exists to avoid overwriting
                        media_filename = f"{unique_id}{file_extension}"
                        media_file = state_media_dir / media_filename
                        if not media_file.exists():
                            # Create a proper record with full metadata from MediaItem
                            from clock import clock
                            from datetime import UTC
                            
                            record = {
                                "unique_id": unique_id,
                                "description": None,
                                "status": MediaStatus.TEMPORARY_FAILURE.value,
                                "failure_reason": "Downloaded from admin console, description pending",
                                "ts": clock.now(UTC).isoformat(),
                            }
                            
                            # Add full metadata from MediaItem if available
                            if media_item:
                                # Add kind (required for proper classification)
                                if hasattr(media_item.kind, "value"):
                                    record["kind"] = media_item.kind.value
                                else:
                                    record["kind"] = str(media_item.kind)
                                
                                # Add sticker-specific metadata
                                if media_item.sticker_set_name:
                                    record["sticker_set_name"] = media_item.sticker_set_name
                                if media_item.sticker_name:
                                    record["sticker_name"] = media_item.sticker_name
                                if media_item.sticker_set_id:
                                    record["sticker_set_id"] = media_item.sticker_set_id
                                if media_item.sticker_access_hash:
                                    record["sticker_access_hash"] = media_item.sticker_access_hash
                                
                                # Add duration for videos/animations
                                if media_item.duration:
                                    record["duration"] = media_item.duration
                            
                            # Add MIME type
                            if mime_type:
                                record["mime_type"] = mime_type
                            
                            try:
                                cache_source.put(unique_id, record, media_bytes, file_extension)
                                logger.debug(
                                    f"Cached media file {media_filename} with full metadata to {state_media_dir} for {unique_id}"
                                )
                            except Exception as e:
                                logger.warning(f"Failed to cache media file {media_filename}: {e}")
                    else:
                        logger.debug(f"Could not determine file extension for {unique_id}, skipping cache")
                except Exception as e:
                    # Don't fail the request if caching fails
                    logger.warning(f"Error caching media file for {unique_id}: {e}")
                
                return Response(
                    media_bytes,
                    mimetype=mime_type or "application/octet-stream",
                    headers={"Content-Disposition": f"inline; filename={unique_id}"}
                )
            except RuntimeError as e:
                error_msg = str(e).lower()
                if "not authenticated" in error_msg or "not running" in error_msg:
                    logger.warning(f"Agent {agent_config_name} client loop issue: {e}")
                    return jsonify({"error": "Agent client loop is not available"}), 503
                else:
                    logger.error(f"Error fetching media: {e}")
                    return jsonify({"error": str(e)}), 500
            except TimeoutError:
                logger.warning(f"Timeout fetching media for agent {agent_config_name}, message {message_id}")
                return jsonify({"error": "Timeout fetching media"}), 504
            except Exception as e:
                logger.error(f"Error fetching media: {e}")
                return jsonify({"error": str(e)}), 500
        except Exception as e:
            logger.error(f"Error getting media for {agent_config_name}/{user_id}/{message_id}/{unique_id}: {e}")
            return jsonify({"error": str(e)}), 500

    @agents_bp.route("/api/agents/<agent_config_name>/conversation/<user_id>/summarize", methods=["POST"])
    def api_trigger_summarization(agent_config_name: str, user_id: str):
        """Trigger summarization for a conversation directly without going through the task graph."""
        try:
            agent = get_agent_by_name(agent_config_name)
            if not agent:
                return jsonify({"error": f"Agent '{agent_config_name}' not found"}), 404

            if not agent.agent_id:
                return jsonify({"error": "Agent not authenticated"}), 400

            try:
                channel_id = int(user_id)
            except ValueError:
                return jsonify({"error": "Invalid user ID"}), 400

            if not agent.client or not agent.client.is_connected():
                return jsonify({"error": "Agent client not connected"}), 503

            # Trigger summarization directly (without going through task graph)
            # This is async, so we need to run it on the agent's event loop
            async def _trigger_summarize():
                await trigger_summarization_directly(agent, channel_id, parse_llm_reply_fn=parse_llm_reply)

            # Use agent.execute() to run the coroutine on the agent's event loop
            try:
                agent.execute(_trigger_summarize(), timeout=60.0)  # Increased timeout for summarization
                return jsonify({"success": True, "message": "Summarization completed successfully"})
            except RuntimeError as e:
                error_msg = str(e).lower()
                if "not authenticated" in error_msg or "not running" in error_msg:
                    logger.warning(f"Agent {agent_config_name} client loop issue: {e}")
                    return jsonify({"error": "Agent client loop is not available"}), 503
                else:
                    logger.error(f"Error triggering summarization: {e}")
                    return jsonify({"error": str(e)}), 500
            except TimeoutError:
                logger.warning(f"Timeout triggering summarization for agent {agent_config_name}, user {user_id}")
                return jsonify({"error": "Timeout triggering summarization"}), 504
        except Exception as e:
            logger.error(f"Error triggering summarization for {agent_config_name}/{user_id}: {e}")
            return jsonify({"error": str(e)}), 500

    @agents_bp.route("/api/agents/<agent_config_name>/conversation/<user_id>/delete-telepathic-messages", methods=["POST"])
    def api_delete_telepathic_messages(agent_config_name: str, user_id: str):
        """Delete all telepathic messages from a channel. Uses agent's client for DMs, puppetmaster for groups."""
        try:
            agent = get_agent_by_name(agent_config_name)
            if not agent:
                return jsonify({"error": f"Agent '{agent_config_name}' not found"}), 404

            try:
                channel_id = int(user_id)
            except ValueError:
                return jsonify({"error": "Invalid user ID"}), 400

            # Check if agent's event loop is accessible (needed to determine DM vs group)
            try:
                client_loop = agent._get_client_loop()
                if not client_loop or not client_loop.is_running():
                    raise RuntimeError("Agent client event loop is not accessible or not running")
            except Exception as e:
                logger.warning(f"Cannot delete telepathic messages - event loop check failed: {e}")
                return jsonify({"error": "Agent client event loop is not available"}), 503

            # Helper function to find and delete telepathic messages
            async def _find_and_delete_telepathic_messages(client, entity, client_name):
                """
                Helper function to find and delete telepathic messages from anyone.
                
                Args:
                    client: The Telegram client to use (agent's client for DMs, puppetmaster's for groups)
                    entity: The channel/group/user entity
                    client_name: Name for logging
                """
                # Collect message IDs to delete
                message_ids_to_delete = []
                
                # Iterate through messages to find telepathic ones
                # Add small delay between fetches to avoid flood waits (0.05s like in run.py)
                message_count = 0
                async for message in client.iter_messages(entity, limit=1000):
                    message_count += 1
                    # Add delay every 20 messages to avoid flood waits
                    if message_count % 20 == 0:
                        await asyncio.sleep(0.05)
                    
                    # Get message text
                    message_text = message.text or ""
                    
                    # Check if message starts with a telepathic prefix (regardless of sender)
                    message_text_stripped = message_text.strip()
                    if message_text_stripped.startswith(TELEPATHIC_PREFIXES):
                        message_ids_to_delete.append(message.id)
                
                logger.info(f"[{client_name}] Found {len(message_ids_to_delete)} telepathic message(s) to delete from channel {entity.id}")
                
                if not message_ids_to_delete:
                    return {"deleted_count": 0, "message": "No telepathic messages found"}
                
                # Delete messages in batches (Telegram API limit is typically 100 messages per request)
                deleted_count = 0
                batch_size = 100
                for i in range(0, len(message_ids_to_delete), batch_size):
                    batch = message_ids_to_delete[i:i + batch_size]
                    try:
                        await client.delete_messages(entity, batch)
                        deleted_count += len(batch)
                        logger.info(f"[{client_name}] Deleted {len(batch)} telepathic messages from channel {entity.id} (message IDs: {batch[:5]}{'...' if len(batch) > 5 else ''})")
                        # Add delay between batches to avoid flood waits
                        if i + batch_size < len(message_ids_to_delete):
                            await asyncio.sleep(0.1)
                    except Exception as e:
                        logger.warning(f"[{client_name}] Error deleting batch of telepathic messages: {e}")
                        # Continue with next batch even if one fails
                        # Add delay even on error to avoid compounding flood waits
                        if i + batch_size < len(message_ids_to_delete):
                            await asyncio.sleep(0.1)
                
                return {"deleted_count": deleted_count, "message": f"Deleted {deleted_count} telepathic message(s)"}

            # First, determine if this is a DM or group/channel
            # We need to do this BEFORE entering the async function to avoid blocking the event loop
            async def _check_if_dm():
                agent_client = agent.client
                if not agent_client or not agent_client.is_connected():
                    raise RuntimeError("Agent client not connected")
                
                # Get entity using agent's client to determine type
                entity_from_agent = await agent_client.get_entity(channel_id)
                
                # Import is_dm to check if this is a DM
                from telegram_util import is_dm
                
                is_direct_message = is_dm(entity_from_agent)
                return is_direct_message, entity_from_agent

            # Check if DM or group (runs on agent's event loop, but quickly)
            try:
                is_direct_message, entity_from_agent = agent.execute(_check_if_dm(), timeout=10.0)
            except RuntimeError as e:
                error_msg = str(e).lower()
                if "not authenticated" in error_msg or "not running" in error_msg:
                    logger.warning(f"Agent {agent_config_name} client loop issue: {e}")
                    return jsonify({"error": "Agent client loop is not available"}), 503
                else:
                    logger.error(f"Error checking channel type: {e}")
                    return jsonify({"error": str(e)}), 500
            except TimeoutError:
                logger.warning(f"Timeout checking channel type for agent {agent_config_name}, user {user_id}")
                return jsonify({"error": "Timeout checking channel type"}), 504

            # Choose the appropriate client: agent for DMs, puppetmaster for groups
            if is_direct_message:
                # Use agent's client for DMs - run async function on agent's event loop
                async def _delete_telepathic_messages_dm():
                    try:
                        agent_client = agent.client
                        if not agent_client or not agent_client.is_connected():
                            raise RuntimeError("Agent client not connected")
                        client_name = f"agent {agent_config_name}"
                        return await _find_and_delete_telepathic_messages(agent_client, entity_from_agent, client_name)
                    except Exception as e:
                        logger.error(f"Error deleting telepathic messages: {e}")
                        raise

                try:
                    result = agent.execute(_delete_telepathic_messages_dm(), timeout=60.0)
                    return jsonify({"success": True, **result})
                except RuntimeError as e:
                    error_msg = str(e).lower()
                    if "not authenticated" in error_msg or "not running" in error_msg:
                        logger.warning(f"Agent {agent_config_name} client loop issue: {e}")
                        return jsonify({"error": "Agent client loop is not available"}), 503
                    else:
                        logger.error(f"Error deleting telepathic messages: {e}")
                        return jsonify({"error": str(e)}), 500
                except TimeoutError:
                    logger.warning(f"Timeout deleting telepathic messages for agent {agent_config_name}, user {user_id}")
                    return jsonify({"error": "Timeout deleting telepathic messages"}), 504
            else:
                # Use puppetmaster's client for groups/channels
                # IMPORTANT: Call puppet_manager.run() from synchronous context to avoid blocking agent's event loop
                from admin_console.puppet_master import (
                    PuppetMasterNotConfigured,
                    PuppetMasterUnavailable,
                    get_puppet_master_manager,
                )
                
                try:
                    puppet_manager = get_puppet_master_manager()
                    puppet_manager.ensure_ready()
                    
                    # Use puppetmaster's run method to execute the deletion
                    # Get entity using puppetmaster's client to ensure compatibility
                    def _delete_with_puppetmaster_factory(puppet_client):
                        async def _delete_with_puppetmaster():
                            # Get entity using puppetmaster's client to avoid "Invalid channel object" error
                            entity = await puppet_client.get_entity(channel_id)
                            return await _find_and_delete_telepathic_messages(puppet_client, entity, "puppetmaster")
                        return _delete_with_puppetmaster()
                    
                    # Call from synchronous context - this blocks the Flask thread, not the agent's event loop
                    result = puppet_manager.run(_delete_with_puppetmaster_factory, timeout=60.0)
                    return jsonify({"success": True, **result})
                except (PuppetMasterNotConfigured, PuppetMasterUnavailable) as e:
                    logger.error(f"Puppet master not available for group deletion: {e}")
                    return jsonify({"error": f"Puppet master not available for group deletion: {e}"}), 503
                except Exception as e:
                    logger.error(f"Error deleting telepathic messages: {e}")
                    return jsonify({"error": str(e)}), 500
        except Exception as e:
            logger.error(f"Error deleting telepathic messages for {agent_config_name}/{user_id}: {e}")
            return jsonify({"error": str(e)}), 500
