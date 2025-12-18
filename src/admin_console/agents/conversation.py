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


def register_conversation_routes(agents_bp: Blueprint):
    """Register conversation management routes."""
    # Import and register routes from split modules
    # Use importlib to avoid circular import issues
    import importlib.util
    from pathlib import Path
    
    agents_dir = Path(__file__).parent
    
    # Load conversation_content
    content_path = agents_dir / "conversation_content.py"
    content_spec = importlib.util.spec_from_file_location("conversation_content", content_path)
    conversation_content = importlib.util.module_from_spec(content_spec)
    content_spec.loader.exec_module(conversation_content)
    
    # Load conversation_actions
    actions_path = agents_dir / "conversation_actions.py"
    actions_spec = importlib.util.spec_from_file_location("conversation_actions", actions_path)
    conversation_actions = importlib.util.module_from_spec(actions_spec)
    actions_spec.loader.exec_module(conversation_actions)
    
    # Load conversation_media
    media_path = agents_dir / "conversation_media.py"
    media_spec = importlib.util.spec_from_file_location("conversation_media", media_path)
    conversation_media = importlib.util.module_from_spec(media_spec)
    media_spec.loader.exec_module(conversation_media)
    
    conversation_content.register_conversation_content_routes(agents_bp)
    conversation_actions.register_conversation_actions_routes(agents_bp)
    conversation_media.register_conversation_media_routes(agents_bp)


# Legacy route implementations moved to separate modules:
# - conversation_content.py: content-check and get-conversation routes
# - conversation_actions.py: translate, xsend, summarize, delete-telepathic-messages routes
# - conversation_media.py: emoji and media serving routes
#
# The original implementation was removed to reduce file size. See the individual modules for route implementations.
