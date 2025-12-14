# utils/ids.py
#
# ID normalization and extraction utilities.

def normalize_peer_id(value):
    """
    Normalize Telegram peer/channel/user IDs:
    - Accepts an int (returns it unchanged)
    - Accepts legacy strings like 'u123' or '123' (returns int 123)
    - Raises ValueError for anything else
    """
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        s = value.strip()
        if s.startswith("u"):
            s = s[1:]
            if s.startswith("-"):
                raise ValueError(f"Unsupported peer id format: {value!r}")
        if s.isdigit():
            return int(s)
        if s.startswith("-") and s[1:].isdigit():
            return -int(s[1:])
    raise ValueError(f"Unsupported peer id format: {value!r}")


def extract_sticker_name_from_document(doc) -> str | None:
    """
    Extract sticker name from a Telegram document object.
    
    Args:
        doc: Telegram document object with attributes
        
    Returns:
        Sticker name (alt attribute) or None if not found
    """
    if not doc:
        return None
        
    attrs = getattr(doc, "attributes", None)
    if not isinstance(attrs, (list, tuple)):
        return None
        
    # Look for alt attribute in document attributes
    for attr in attrs:
        if hasattr(attr, "alt"):
            alt = getattr(attr, "alt", None)
            if isinstance(alt, str) and alt.strip():
                return alt.strip()
                
    return None


async def get_custom_emoji_name(agent, document_id) -> str:
    """
    Get the description of a custom emoji from its document ID using the media pipeline.
    
    Args:
        agent: The agent instance with client access
        document_id: Telegram document ID for the custom emoji
        
    Returns:
        Custom emoji description in ⟦media⟧ format, or fallback placeholder
    """
    # Log at module level first
    import logging
    logger = logging.getLogger(__name__)
    logger.info(f"DEBUG: get_custom_emoji_name called with document_id={document_id}")
    
    try:
        from media.media_source import get_default_media_source_chain
        from telegram_media import get_unique_id
        from telethon.tl.functions.messages import GetCustomEmojiDocumentsRequest
        
        # Try to get the document from the agent's client using the correct API
        result = await agent.client(GetCustomEmojiDocumentsRequest(document_id=[document_id]))
        if not result or len(result) == 0:
            logger.info(f"DEBUG: get_custom_emoji_name: No document found for {document_id}")
            return "🎭"  # Fallback if document not found
        
        doc_obj = result[0]
        unique_id = get_unique_id(doc_obj)
        if not unique_id:
            logger.info(f"DEBUG: get_custom_emoji_name: No unique_id for {document_id}")
            return "🎭"  # Fallback if no unique ID
        
        logger.info(f"Custom emoji in reaction: document_id={document_id}, unique_id={unique_id}")
        
        # Extract sticker metadata
        sticker_name = extract_sticker_name_from_document(doc_obj)
        sticker_set_name = None
        sticker_set_id = None
        sticker_access_hash = None
        
        attrs = getattr(doc_obj, "attributes", None)
        if isinstance(attrs, (list, tuple)):
            for a in attrs:
                if hasattr(a, "stickerset"):
                    ss = getattr(a, "stickerset", None)
                    if ss:
                        sticker_set_name = getattr(ss, "short_name", None)
                        sticker_set_id = getattr(ss, "id", None)
                        sticker_access_hash = getattr(ss, "access_hash", None)
        
        # If we have sticker_set_id but no short_name, query the set to get the name, title, and emoji status
        sticker_set_title = None
        is_emoji_set = None
        
        if sticker_set_id and not sticker_set_name:
            try:
                from telethon.tl.functions.messages import GetStickerSetRequest
                from telethon.tl.types import InputStickerSetID
                
                logger.info(f"DEBUG: Querying sticker set for custom emoji {document_id}: set_id={sticker_set_id}")
                
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
                        logger.info(f"DEBUG: Got sticker set info for custom emoji {document_id}: name={sticker_set_name}, title={sticker_set_title}, is_emoji_set={is_emoji_set}")
            except Exception as e:
                logger.info(f"DEBUG: Failed to query sticker set for custom emoji {document_id}: {e}")
        
        # Use media pipeline to get the description
        media_chain = get_default_media_source_chain()
        
        logger.info(f"Calling media pipeline for reaction custom emoji {document_id}: unique_id={unique_id}, sticker_set={sticker_set_name}, is_emoji_set={is_emoji_set}, sticker_name={sticker_name}")
        
        # Build metadata dict to pass additional fields
        metadata = {}
        if sticker_set_title is not None:
            metadata['sticker_set_title'] = sticker_set_title
        if is_emoji_set is not None:
            metadata['is_emoji_set'] = is_emoji_set
        
        record = await media_chain.get(
            unique_id=unique_id,
            agent=agent,
            doc=doc_obj,
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
        
        if record and record.get("description"):
            # Return in ⟦media⟧ format
            logger.info(f"Media pipeline returned description for reaction custom emoji {document_id}: {record.get('description')[:50]}")
            return f"⟦media⟧ {record['description']}"
        
        logger.info(f"Media pipeline returned no description for reaction custom emoji {document_id}, using fallback")
        
        # Fallback: use sticker name if available
        if sticker_name:
            return f"⟦media⟧ {sticker_name} custom emoji"
        
    except Exception as e:
        # Log error but don't fail - just use fallback
        logger.info(f"DEBUG: Error in get_custom_emoji_name for {document_id}: {type(e).__name__}: {e}")
        import traceback
        logger.info(f"DEBUG: Traceback: {traceback.format_exc()}")
    
    logger.info(f"DEBUG: get_custom_emoji_name returning fallback for {document_id}")
    return "🎭"  # Fallback placeholder


def extract_user_id_from_peer(peer_id) -> int | None:
    """
    Extract user ID from a Telegram peer object.
    
    Args:
        peer_id: Telegram peer object (may have user_id, channel_id, or chat_id attributes)
        
    Returns:
        User ID as integer, or None if not found
    """
    if not peer_id:
        return None
        
    # Try different ID attributes in order of preference
    for attr in ("user_id", "channel_id", "chat_id"):
        user_id = getattr(peer_id, attr, None)
        if isinstance(user_id, int):
            return user_id
            
    return None
