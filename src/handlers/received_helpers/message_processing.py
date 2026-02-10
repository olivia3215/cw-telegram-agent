# src/handlers/received_helpers/message_processing.py
#
# Copyright (c) 2025-2026 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.
#
import logging
from dataclasses import dataclass

from llm.base import MsgPart
from media.media_injector import format_message_for_prompt
from telepathic import TELEPATHIC_PREFIXES
from utils import format_username, get_channel_name

logger = logging.getLogger(__name__)


@dataclass
class ProcessedMessage:
    """Represents a processed message with all its components for LLM history."""

    message_parts: list[MsgPart]
    sender_display: str
    sender_id: str
    sender_username: str | None
    message_id: str
    is_from_agent: bool
    reply_to_msg_id: str | None = None
    timestamp: str | None = None  # Agent-local timestamp string
    reactions: str | None = None  # Formatted reactions string


async def format_message_reactions(agent, message) -> str | None:
    """
    Format reactions for a message.
    
    Args:
        agent: The agent instance
        message: Telegram message object
        
    Returns:
        Formatted reactions string like '"Wendy"(1234)=❤️, "Cindy"(5678)=👍' or None if no reactions
    """
    try:
        message_id = getattr(message, 'id', 'unknown')
        
        reactions_obj = getattr(message, 'reactions', None)
        if not reactions_obj:
            return None
        
        logger.debug(f"Processing reactions for message {message_id}")
            
        # Get recent reactions if available
        recent_reactions = getattr(reactions_obj, 'recent_reactions', None)
        if not recent_reactions:
            logger.debug(f"No recent_reactions for message {message_id}")
            return None
        
        logger.debug(f"Message {message_id} has {len(recent_reactions)} reaction(s)")
            
        reaction_parts = []
        for idx, reaction in enumerate(recent_reactions):
            # Get user info
            peer_id = getattr(reaction, 'peer_id', None)
            if not peer_id:
                logger.debug(f"Reaction {idx} on message {message_id} has no peer_id")
                continue
                
            # Get user ID from peer
            from utils import extract_user_id_from_peer, get_custom_emoji_name
            user_id = extract_user_id_from_peer(peer_id)
            if user_id is None:
                logger.debug(f"Reaction {idx} on message {message_id} has no user_id")
                continue
                
            # Get user name
            user_name = await get_channel_name(agent, user_id)
            logger.debug(f"Reaction {idx} on message {message_id} from {user_name}({user_id})")
                
            # Get reaction emoji
            reaction_obj = getattr(reaction, 'reaction', None)
            if not reaction_obj:
                logger.debug(f"Reaction {idx} on message {message_id} has no reaction object")
                continue
                
            emoji = None
            if hasattr(reaction_obj, 'emoticon'):
                emoji = reaction_obj.emoticon
                logger.debug(f"Reaction {idx} on message {message_id} is standard emoji: {emoji}")
            elif hasattr(reaction_obj, 'document_id'):
                # Custom emoji - get the sticker name
                doc_id = reaction_obj.document_id
                logger.debug(f"Reaction {idx} on message {message_id} is custom emoji with document_id={doc_id}")
                emoji = await get_custom_emoji_name(agent, doc_id)
                logger.debug(f"Reaction {idx} on message {message_id} custom emoji resolved to: {emoji[:100] if emoji else None}")
            else:
                logger.debug(f"Reaction {idx} on message {message_id} has unknown reaction type: {type(reaction_obj)}")
                
            if emoji:
                reaction_parts.append(f'"{user_name}"({user_id})={emoji}')
        
        result = ', '.join(reaction_parts) if reaction_parts else None
        if result:
            logger.debug(f"Formatted reactions for message {message_id}: {result[:100]}")
        return result
        
    except Exception as e:
        logger.debug(f"Error formatting reactions for message {getattr(message, 'id', 'unknown')}: {e}")
        return None


async def process_message_history(
    messages, agent, media_chain
) -> list[ProcessedMessage]:
    """
    Convert Telegram messages to ProcessedMessage objects.

    Args:
        messages: List of Telegram messages (newest first)
        agent: The agent instance
        media_chain: Media source chain for formatting

    Returns:
        List of ProcessedMessage objects in chronological order (oldest first)
    """
    from telepathic import is_telepath
    
    history_rendered_items: list[ProcessedMessage] = []
    chronological = list(reversed(messages))  # oldest → newest

    for m in chronological:
        message_parts = await format_message_for_prompt(
            m, agent=agent, media_chain=media_chain
        )
        if not message_parts:
            continue

        # Filter out telepathic messages from agent's view
        # Check if this is a telepathic message (starts with ⟦think⟧, ⟦remember⟧, ⟦intend⟧, ⟦plan⟧, ⟦retrieve⟧, or ⟦summarize⟧)
        # Note: ⟦media⟧ is NOT a telepathic prefix - it's used for legitimate media descriptions
        message_text = ""
        for part in message_parts:
            if part.get("kind") == "text":
                text_val = part.get("text", "")
                message_text += str(text_val) if text_val else ""
            elif part.get("kind") == "media":
                rendered_val = part.get("rendered_text", "")
                message_text += str(rendered_val) if rendered_val else ""
        
        # Check if message starts with a telepathic prefix (explicit list, not regex, to avoid matching ⟦media⟧)
        message_text_stripped = message_text.strip()
        is_telepathic_message = message_text_stripped.startswith(TELEPATHIC_PREFIXES)
        
        if not is_telepath(agent.agent_id) and is_telepathic_message:
            logger.debug(f"[telepathic] Filtering out telepathic message from agent view: {message_text_stripped[:50]}...")
            continue

        # Get sender information
        sender_id_val = getattr(m, "sender_id", None)
        sender_id = str(sender_id_val) if sender_id_val is not None else "unknown"
        sender_display = (
            await get_channel_name(agent, sender_id_val) if sender_id_val else "unknown"
        )
        sender_username = None
        sender_entity = getattr(m, "sender", None)
        if sender_entity is None and sender_id_val is not None:
            try:
                sender_entity = await agent.get_cached_entity(sender_id_val)
            except Exception:
                sender_entity = None
        if sender_entity is not None:
            sender_username = format_username(sender_entity)
 
        message_id = str(getattr(m, "id", ""))
        is_from_agent = bool(getattr(m, "out", False))

        # Extract reply_to information
        reply_to_msg_id = None
        reply_to = getattr(m, "reply_to", None)
        if reply_to:
            reply_to_msg_id_val = getattr(reply_to, "reply_to_msg_id", None)
            if reply_to_msg_id_val is not None:
                reply_to_msg_id = str(reply_to_msg_id_val)

        # Extract and format timestamp
        timestamp_str = None
        msg_date = getattr(m, "date", None)
        if msg_date:
            if msg_date.tzinfo is None:
                from datetime import UTC
                msg_date = msg_date.replace(tzinfo=UTC)
            local_time = msg_date.astimezone(agent.timezone)
            timestamp_str = local_time.strftime("%Y-%m-%d %H:%M:%S %Z")

        # Format reactions
        reactions_str = await format_message_reactions(agent, m)

        history_rendered_items.append(
            ProcessedMessage(
                message_parts=message_parts,
                sender_display=sender_display,
                sender_id=sender_id,
                sender_username=sender_username,
                message_id=message_id,
                is_from_agent=is_from_agent,
                reply_to_msg_id=reply_to_msg_id,
                timestamp=timestamp_str,
                reactions=reactions_str,
            )
        )

    return history_rendered_items
