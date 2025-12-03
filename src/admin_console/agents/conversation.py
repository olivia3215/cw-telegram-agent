# admin_console/agents/conversation.py
#
# Conversation management routes for the admin console.

import asyncio
import copy
import json as json_lib
import logging
import os
import re
from pathlib import Path

from flask import Blueprint, Response, jsonify, request  # pyright: ignore[reportMissingImports]

from admin_console.helpers import get_agent_by_name
from config import STATE_DIRECTORY
from handlers.received_helpers.message_processing import format_message_reactions
from handlers.received import parse_llm_reply
from handlers.received_helpers.summarization import trigger_summarization_directly
from llm.media_helper import get_media_llm
from memory_storage import load_property_entries
from media.media_injector import format_message_for_prompt
from media.media_source import get_default_media_source_chain
from media.mime_utils import detect_mime_type_from_bytes
from task_graph import WorkQueue
from task_graph_helpers import insert_received_task_for_conversation
from telegram_download import download_media_bytes
from telegram_media import iter_media_parts
from telegram_util import get_channel_name
from telepathic import TELEPATHIC_PREFIXES

logger = logging.getLogger(__name__)

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
                        "description": "The English translation of the message text"
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


def _get_highest_summarized_message_id_for_api(agent_name: str, channel_id: int) -> int | None:
    """
    Get the highest message ID that has been summarized (for use in Flask context).
    
    Everything with message ID <= this value can be assumed to be summarized.
    Returns None if no summaries exist.
    """
    try:
        summary_file = Path(STATE_DIRECTORY) / agent_name / "memory" / f"{channel_id}.json"
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
        logger.debug(f"Failed to get highest summarized message ID for {agent_name}/{channel_id}: {e}")
        return None


def _has_conversation_content_local(agent_name: str, channel_id: int) -> bool:
    """
    Check if a conversation has content by checking local files only (no Telegram API calls).
    
    Returns True if summaries exist or if the summary file exists (indicating conversation data).
    """
    try:
        summary_file = Path(STATE_DIRECTORY) / agent_name / "memory" / f"{channel_id}.json"
        if not summary_file.exists():
            return False
        
        summaries, _ = load_property_entries(summary_file, "summary", default_id_prefix="summary")
        # If summaries exist, there's conversation content
        return len(summaries) > 0
    except Exception:
        return False


def register_conversation_routes(agents_bp: Blueprint):
    """Register conversation management routes."""
    
    @agents_bp.route("/api/agents/<agent_name>/conversation-content-check", methods=["POST"])
    def api_check_conversation_content_batch(agent_name: str):
        """
        Batch check which partners have conversation content (local files only, no Telegram API calls).
        
        Request body: {"user_ids": ["user_id1", "user_id2", ...]}
        Response: {"content_checks": {"user_id1": true, "user_id2": false, ...}}
        """
        try:
            agent = get_agent_by_name(agent_name)
            if not agent:
                return jsonify({"error": f"Agent '{agent_name}' not found"}), 404

            data = request.json or {}
            user_ids = data.get("user_ids", [])
            
            if not isinstance(user_ids, list):
                return jsonify({"error": "user_ids must be a list"}), 400

            content_checks = {}
            for user_id_str in user_ids:
                try:
                    channel_id = int(user_id_str)
                    content_checks[user_id_str] = _has_conversation_content_local(agent_name, channel_id)
                except (ValueError, TypeError):
                    content_checks[user_id_str] = False

            return jsonify({"content_checks": content_checks})
        except Exception as e:
            logger.error(f"Error checking conversation content for {agent_name}: {e}")
            return jsonify({"error": str(e)}), 500

    @agents_bp.route("/api/agents/<agent_name>/conversation/<user_id>", methods=["GET"])
    def api_get_conversation(agent_name: str, user_id: str):
        """Get conversation history (unsummarized messages only) and summaries."""
        try:
            agent = get_agent_by_name(agent_name)
            if not agent:
                return jsonify({"error": f"Agent '{agent_name}' not found"}), 404

            if not agent.client or not agent.client.is_connected():
                return jsonify({"error": "Agent client not connected"}), 503

            try:
                channel_id = int(user_id)
            except ValueError:
                return jsonify({"error": "Invalid user ID"}), 400

            # Get summaries
            summary_file = Path(STATE_DIRECTORY) / agent_name / "memory" / f"{channel_id}.json"
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
                        logger.warning(f"Backfill failed for {agent_name}/{user_id}: {e}", exc_info=True)
                
                # Schedule backfill in agent's thread (non-blocking, fire-and-forget)
                executor = agent.executor
                if executor and executor.loop and executor.loop.is_running():
                    # Schedule the coroutine without waiting for it
                    asyncio.run_coroutine_threadsafe(_backfill_dates(), executor.loop)
                    logger.info(f"Scheduled backfill for {agent_name}/{user_id} (channel {channel_id})")
                else:
                    logger.info(
                        f"Agent executor not available for {agent_name}, skipping backfill. "
                        f"executor={executor}, loop={executor.loop if executor else None}, "
                        f"is_running={executor.loop.is_running() if executor and executor.loop else None}"
                    )
            except Exception as e:
                # Don't fail the request if backfill setup fails
                logger.warning(f"Failed to setup backfill for {agent_name}/{user_id}: {e}", exc_info=True)
            
            # Get highest summarized message ID to filter messages
            highest_summarized_id = _get_highest_summarized_message_id_for_api(agent_name, channel_id)

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
                                f"[{agent_name}] Unexpected: message {msg_id} <= highest_summarized_id {highest_summarized_id} "
                                f"despite min_id filter"
                            )
                            continue
                        
                        from_id = getattr(message, "from_id", None)
                        sender_id = None
                        if from_id:
                            sender_id = getattr(from_id, "user_id", None) or getattr(from_id, "channel_id", None)
                        is_from_agent = sender_id == agent.agent_id
                        
                        # Get sender name
                        sender_name = None
                        if sender_id and isinstance(sender_id, int):
                            try:
                                sender_name = await get_channel_name(agent, sender_id)
                            except Exception as e:
                                logger.debug(f"Failed to get sender name for {sender_id}: {e}")
                                sender_name = None
                        
                        text = message.text or ""
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
                        
                        # Format media/stickers
                        message_parts = await format_message_for_prompt(message, agent=agent, media_chain=media_chain)
                        
                        # Build message parts list (text and media)
                        parts = []
                        for part in message_parts:
                            if part.get("kind") == "text":
                                parts.append({
                                    "kind": "text",
                                    "text": part.get("text", "")
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
                        
                        messages.append({
                            "id": str(message.id),
                            "text": text,
                            "parts": parts,  # Include formatted parts (text + media)
                            "sender_id": str(sender_id) if sender_id else None,
                            "sender_name": sender_name,
                            "is_from_agent": is_from_agent,
                            "timestamp": timestamp,
                            "reply_to_msg_id": reply_to_msg_id,
                            "reactions": reactions_str,
                        })
                    logger.info(
                        f"[{agent_name}] Fetched {total_fetched} unsummarized messages for channel {channel_id} "
                        f"(highest_summarized_id={highest_summarized_id}, using min_id filter)"
                    )
                    return list(reversed(messages))  # Return in chronological order
                except Exception as e:
                    logger.error(f"Error fetching messages for {agent_name}/{channel_id}: {e}", exc_info=True)
                    return []

            # Use agent.execute() to run the coroutine on the agent's event loop
            try:
                messages = agent.execute(_get_messages(), timeout=30.0)
                return jsonify({"messages": messages, "summaries": summaries})
            except RuntimeError as e:
                error_msg = str(e).lower()
                if "not authenticated" in error_msg or "not running" in error_msg:
                    logger.warning(f"Agent {agent_name} client loop issue: {e}")
                    return jsonify({"error": "Agent client loop is not available"}), 503
                else:
                    logger.error(f"Error fetching conversation: {e}")
                    return jsonify({"error": str(e)}), 500
            except TimeoutError:
                logger.warning(f"Timeout fetching conversation for agent {agent_name}, user {user_id}")
                return jsonify({"error": "Timeout fetching conversation"}), 504
            except Exception as e:
                logger.error(f"Error fetching conversation: {e}")
                return jsonify({"error": str(e)}), 500
        except Exception as e:
            logger.error(f"Error getting conversation for {agent_name}/{user_id}: {e}")
            return jsonify({"error": str(e)}), 500

    @agents_bp.route("/api/agents/<agent_name>/conversation/<user_id>/translate", methods=["POST"])
    def api_translate_conversation(agent_name: str, user_id: str):
        """Translate unsummarized messages into English using the media LLM."""
        try:
            agent = get_agent_by_name(agent_name)
            if not agent:
                return jsonify({"error": f"Agent '{agent_name}' not found"}), 404

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

            # Get media LLM
            try:
                media_llm = get_media_llm()
            except Exception as e:
                logger.error(f"Failed to get media LLM: {e}")
                return jsonify({"error": "Media LLM not available"}), 503

            # Build translation prompt with messages as structured JSON
            # This avoids issues with unescaped quotes/newlines in message text
            messages_for_prompt = []
            for msg in messages:
                msg_id = msg.get("id", "")
                msg_text = msg.get("text", "")
                if msg_text:
                    messages_for_prompt.append({
                        "message_id": str(msg_id),
                        "text": msg_text
                    })
            
            # Convert to JSON string for the prompt (properly escaped)
            import json as json_module
            messages_json = json_module.dumps(messages_for_prompt, ensure_ascii=False, indent=2)
            
            translation_prompt = (
                "Translate the conversation messages into English.\n"
                "Preserve the message structure and return a JSON object with translations.\n"
                "\n"
                "Return a JSON object with this structure:\n"
                "{\n"
                "  \"translations\": [\n"
                "    {\"message_id\": \"123\", \"translated_text\": \"English translation here\"},\n"
                "    ...\n"
                "  ]\n"
                "}\n"
                "\n"
                "Translate all messages provided, maintaining the order and message IDs. Ensure all JSON is properly formatted."
                "\n"
                "Input messages (as JSON):\n"
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
                    
                    result_text = await media_llm.query_with_json_schema(
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
                translation_dict = {t["message_id"]: t["translated_text"] for t in translations}
                
                return jsonify({"translations": translation_dict})
            except RuntimeError as e:
                error_msg = str(e).lower()
                if "not authenticated" in error_msg or "not running" in error_msg:
                    logger.warning(f"Agent {agent_name} client loop issue: {e}")
                    return jsonify({"error": "Agent client loop is not available"}), 503
                else:
                    logger.error(f"Error translating conversation: {e}")
                    return jsonify({"error": str(e)}), 500
            except TimeoutError:
                logger.warning(f"Timeout translating conversation for agent {agent_name}, user {user_id}")
                return jsonify({"error": "Timeout translating conversation"}), 504
            except Exception as e:
                logger.error(f"Error translating conversation: {e}")
                return jsonify({"error": str(e)}), 500
        except Exception as e:
            logger.error(f"Error translating conversation for {agent_name}/{user_id}: {e}")
            return jsonify({"error": str(e)}), 500

    @agents_bp.route("/api/agents/<agent_name>/xsend/<user_id>", methods=["POST"])
    def api_xsend(agent_name: str, user_id: str):
        """Create an xsend task to trigger a received task on another channel."""
        try:
            agent = get_agent_by_name(agent_name)
            if not agent:
                return jsonify({"error": f"Agent '{agent_name}' not found"}), 404

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
                    logger.warning(f"Agent {agent_name} client loop issue: {e}")
                    return jsonify({"error": "Agent client loop is not available"}), 503
                else:
                    logger.error(f"Error creating xsend task: {e}")
                    return jsonify({"error": str(e)}), 500
            except TimeoutError:
                logger.warning(f"Timeout creating xsend task for agent {agent_name}, user {user_id}")
                return jsonify({"error": "Timeout creating xsend task"}), 504
        except Exception as e:
            logger.error(f"Error creating xsend task for {agent_name}/{user_id}: {e}")
            return jsonify({"error": str(e)}), 500

    @agents_bp.route("/api/agents/<agent_name>/conversation/<user_id>/media/<message_id>/<unique_id>", methods=["GET"])
    def api_get_conversation_media(agent_name: str, user_id: str, message_id: str, unique_id: str):
        """Serve media from a Telegram message."""
        try:
            agent = get_agent_by_name(agent_name)
            if not agent:
                return jsonify({"error": f"Agent '{agent_name}' not found"}), 404

            if not agent.client or not agent.client.is_connected():
                return jsonify({"error": "Agent client not connected"}), 503

            try:
                channel_id = int(user_id)
                msg_id = int(message_id)
            except ValueError:
                return jsonify({"error": "Invalid user ID or message ID"}), 400

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
                        return None, None
                    
                    # Handle case where get_messages returns a list
                    if isinstance(message, list):
                        if len(message) == 0:
                            return None, None
                        message = message[0]
                    
                    # Find the media item with matching unique_id
                    media_items = iter_media_parts(message)
                    for item in media_items:
                        if item.unique_id == unique_id:
                            # Download media bytes
                            media_bytes = await download_media_bytes(client, item.file_ref)
                            # Detect MIME type
                            mime_type = detect_mime_type_from_bytes(media_bytes[:1024])
                            return media_bytes, mime_type
                    
                    return None, None
                except Exception as e:
                    logger.error(f"Error fetching media: {e}")
                    return None, None

            # Use agent.execute() to run the coroutine on the agent's event loop
            try:
                media_bytes, mime_type = agent.execute(_get_media(), timeout=30.0)
                if media_bytes is None:
                    return jsonify({"error": "Media not found"}), 404
                
                return Response(
                    media_bytes,
                    mimetype=mime_type or "application/octet-stream",
                    headers={"Content-Disposition": f"inline; filename={unique_id}"}
                )
            except RuntimeError as e:
                error_msg = str(e).lower()
                if "not authenticated" in error_msg or "not running" in error_msg:
                    logger.warning(f"Agent {agent_name} client loop issue: {e}")
                    return jsonify({"error": "Agent client loop is not available"}), 503
                else:
                    logger.error(f"Error fetching media: {e}")
                    return jsonify({"error": str(e)}), 500
            except TimeoutError:
                logger.warning(f"Timeout fetching media for agent {agent_name}, message {message_id}")
                return jsonify({"error": "Timeout fetching media"}), 504
            except Exception as e:
                logger.error(f"Error fetching media: {e}")
                return jsonify({"error": str(e)}), 500
        except Exception as e:
            logger.error(f"Error getting media for {agent_name}/{user_id}/{message_id}/{unique_id}: {e}")
            return jsonify({"error": str(e)}), 500

    @agents_bp.route("/api/agents/<agent_name>/conversation/<user_id>/summarize", methods=["POST"])
    def api_trigger_summarization(agent_name: str, user_id: str):
        """Trigger summarization for a conversation directly without going through the task graph."""
        try:
            agent = get_agent_by_name(agent_name)
            if not agent:
                return jsonify({"error": f"Agent '{agent_name}' not found"}), 404

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
                    logger.warning(f"Agent {agent_name} client loop issue: {e}")
                    return jsonify({"error": "Agent client loop is not available"}), 503
                else:
                    logger.error(f"Error triggering summarization: {e}")
                    return jsonify({"error": str(e)}), 500
            except TimeoutError:
                logger.warning(f"Timeout triggering summarization for agent {agent_name}, user {user_id}")
                return jsonify({"error": "Timeout triggering summarization"}), 504
        except Exception as e:
            logger.error(f"Error triggering summarization for {agent_name}/{user_id}: {e}")
            return jsonify({"error": str(e)}), 500

    @agents_bp.route("/api/agents/<agent_name>/conversation/<user_id>/delete-telepathic-messages", methods=["POST"])
    def api_delete_telepathic_messages(agent_name: str, user_id: str):
        """Delete all telepathic messages from a channel. Uses agent's client for DMs, puppetmaster for groups."""
        try:
            agent = get_agent_by_name(agent_name)
            if not agent:
                return jsonify({"error": f"Agent '{agent_name}' not found"}), 404

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
                    logger.warning(f"Agent {agent_name} client loop issue: {e}")
                    return jsonify({"error": "Agent client loop is not available"}), 503
                else:
                    logger.error(f"Error checking channel type: {e}")
                    return jsonify({"error": str(e)}), 500
            except TimeoutError:
                logger.warning(f"Timeout checking channel type for agent {agent_name}, user {user_id}")
                return jsonify({"error": "Timeout checking channel type"}), 504

            # Choose the appropriate client: agent for DMs, puppetmaster for groups
            if is_direct_message:
                # Use agent's client for DMs - run async function on agent's event loop
                async def _delete_telepathic_messages_dm():
                    try:
                        agent_client = agent.client
                        if not agent_client or not agent_client.is_connected():
                            raise RuntimeError("Agent client not connected")
                        client_name = f"agent {agent_name}"
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
                        logger.warning(f"Agent {agent_name} client loop issue: {e}")
                        return jsonify({"error": "Agent client loop is not available"}), 503
                    else:
                        logger.error(f"Error deleting telepathic messages: {e}")
                        return jsonify({"error": str(e)}), 500
                except TimeoutError:
                    logger.warning(f"Timeout deleting telepathic messages for agent {agent_name}, user {user_id}")
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
            logger.error(f"Error deleting telepathic messages for {agent_name}/{user_id}: {e}")
            return jsonify({"error": str(e)}), 500

