# admin_console/agents/conversation_actions.py
#
# Conversation action routes for the admin console (translate, xsend, summarize, delete-telepathic-messages).

import asyncio
import copy
import html
import json as json_lib
import logging
import os
import re
from datetime import datetime, timedelta
from pathlib import Path

from flask import Blueprint, jsonify, request, Response, stream_with_context  # pyright: ignore[reportMissingImports]

from admin_console.helpers import get_agent_by_name
from config import STATE_DIRECTORY, TRANSLATION_MODEL
from handlers.received import parse_llm_reply
from llm.factory import create_llm_from_name
from handlers.received_helpers.summarization import trigger_summarization_directly
from task_graph import WorkQueue
from task_graph_helpers import insert_received_task_for_conversation
from telepathic import TELEPATHIC_PREFIXES

# Import markdown_to_html and placeholder functions from conversation module
# Use importlib since this module is loaded dynamically by conversation.py
import importlib.util
from pathlib import Path
_conversation_path = Path(__file__).parent / "conversation.py"
_conversation_spec = importlib.util.spec_from_file_location("conversation", _conversation_path)
_conversation_mod = importlib.util.module_from_spec(_conversation_spec)
_conversation_spec.loader.exec_module(_conversation_mod)
markdown_to_html = _conversation_mod.markdown_to_html
replace_html_tags_with_placeholders = _conversation_mod.replace_html_tags_with_placeholders
restore_html_tags_from_placeholders = _conversation_mod.restore_html_tags_from_placeholders

logger = logging.getLogger(__name__)


# Import the centralized helper function
from admin_console.helpers import resolve_user_id_to_channel_id as _resolve_user_id_to_channel_id

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


def register_conversation_actions_routes(agents_bp: Blueprint):
    """Register conversation action routes."""
    
    @agents_bp.route("/api/agents/<agent_config_name>/conversation/<user_id>/translate", methods=["POST"])
    def api_translate_conversation(agent_config_name: str, user_id: str):
        """Translate unsummarized messages into English using the media LLM. Streams translations via SSE."""
        try:
            agent = get_agent_by_name(agent_config_name)
            if not agent:
                return jsonify({"error": f"Agent '{agent_config_name}' not found"}), 404

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

            def generate_translations():
                """Generator function that yields SSE events for translations."""
                try:
                    # Build mapping from text to message_id(s) - same text can appear in multiple messages
                    # Also build list of messages that need translation
                    text_to_message_ids: dict[str, list[str]] = {}
                    messages_to_translate: list[dict[str, str]] = []  # List of {message_id, text}
                    message_id_to_text: dict[str, str] = {}  # Map message_id to original text for lookup
                    
                    for msg in messages:
                        msg_id = str(msg.get("id", ""))
                        # Use HTML text (already XSS-protected from markdown_to_html)
                        msg_text = msg.get("text", "")
                        if not msg_text:
                            continue
                        
                        # Track this message ID for this text
                        if msg_text not in text_to_message_ids:
                            text_to_message_ids[msg_text] = []
                        text_to_message_ids[msg_text].append(msg_id)
                        message_id_to_text[msg_id] = msg_text
                        
                        # Check for existing translation in MySQL
                        translation = None
                        try:
                            from translation_cache import get_translation
                            translation = get_translation(msg_text)
                        except Exception:
                            pass
                        
                        # If not found in cache, add to translation list
                        if translation is None:
                            # Only add if we haven't already added this text
                            if not any(m["text"] == msg_text for m in messages_to_translate):
                                messages_to_translate.append({
                                    "message_id": msg_id,  # Use first message_id for this text
                                    "text": msg_text  # HTML text (already XSS-protected)
                                })

                    # Build result dict from cache for messages we have cached
                    # Translations in cache are stored as final HTML (tags already restored)
                    cached_translations: dict[str, str] = {}
                    for msg in messages:
                        msg_id = str(msg.get("id", ""))
                        msg_text = msg.get("text", "")  # HTML text (already XSS-protected)
                        if not msg_text:
                            continue
                        
                        # Check for cached translation in MySQL
                        translated_text = None
                        try:
                            from translation_cache import get_translation
                            translated_text = get_translation(msg_text)
                        except Exception:
                            pass
                        
                        if translated_text:
                            # Translation is already final HTML (tags restored)
                            cached_translations[msg_id] = translated_text

                    # Send cached translations immediately as first event
                    if cached_translations:
                        yield f"data: {json_lib.dumps({'type': 'cached', 'translations': cached_translations})}\n\n"

                    # If we have messages to translate, batch them (max 10 per batch)
                    if messages_to_translate:
                        # Use the translation LLM specified by TRANSLATION_MODEL environment variable
                        if not TRANSLATION_MODEL:
                            raise ValueError(
                                "TRANSLATION_MODEL environment variable is required for translation. "
                                "Set TRANSLATION_MODEL to specify the model for translations."
                            )
                        translation_llm = create_llm_from_name(TRANSLATION_MODEL)
                        
                        # Batch size: max 10 messages
                        batch_size = 10
                        batches = [
                            messages_to_translate[i:i + batch_size]
                            for i in range(0, len(messages_to_translate), batch_size)
                        ]
                        
                        # This is async, so we need to run it in the client's event loop
                        async def _translate_batch(batch: list[dict[str, str]]) -> list[dict[str, str]]:
                            """Translate a batch of messages."""
                            try:
                                # Replace HTML tags with placeholders before sending to LLM
                                # This prevents XSS and simplifies translation
                                batch_with_placeholders = []
                                batch_tag_maps = {}  # Store tag maps for each message
                                
                                for msg in batch:
                                    message_id = msg["message_id"]
                                    html_text = msg["text"]
                                    
                                    # Replace HTML tags with placeholders
                                    text_with_placeholders, tag_map = replace_html_tags_with_placeholders(html_text)
                                    
                                    # Store tag map for later restoration
                                    batch_tag_maps[message_id] = tag_map
                                    
                                    # Add message with placeholders to batch
                                    batch_with_placeholders.append({
                                        "message_id": message_id,
                                        "text": text_with_placeholders
                                    })
                                
                                # Helper function to restore HTML tags in translations
                                def _restore_html_tags_in_translations(translations: list[dict[str, str]]) -> list[dict[str, str]]:
                                    """Restore HTML tags from placeholders in a list of translations."""
                                    restored_translations = []
                                    for translation in translations:
                                        message_id = translation.get("message_id")
                                        translated_text_with_placeholders = translation.get("translated_text", "")
                                        
                                        if message_id and message_id in batch_tag_maps:
                                            # Restore HTML tags
                                            tag_map = batch_tag_maps[message_id]
                                            restored_text = restore_html_tags_from_placeholders(
                                                translated_text_with_placeholders, tag_map
                                            )
                                            restored_translations.append({
                                                "message_id": message_id,
                                                "translated_text": restored_text  # Final HTML with tags restored
                                            })
                                        else:
                                            # No tag map (shouldn't happen, but handle gracefully)
                                            restored_translations.append(translation)
                                    return restored_translations
                                
                                # Build translation prompt with messages (now with placeholders instead of HTML)
                                import json as json_module
                                messages_json = json_module.dumps(batch_with_placeholders, ensure_ascii=False, indent=2)
                                
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
                                    "Translate all messages provided, maintaining the order and message IDs. "
                                    "The messages contain placeholder tags like <HTMLTAG1>, <HTMLTAG2>, etc. "
                                    "Do NOT modify these placeholders - preserve them exactly as they appear. "
                                    "Translate only the text content between placeholders. Ensure all JSON is properly formatted."
                                    "\n"
                                    "Input messages (as JSON, with placeholder tags):\n"
                                    f"{messages_json}\n"
                                )
                                
                                # Use the shared query_with_json_schema API for LLM-agnostic translation
                                system_prompt = (
                                    "You are a translation assistant. Translate messages into English and return JSON.\n\n"
                                    f"{translation_prompt}"
                                )
                                
                                result_text = await translation_llm.query_with_json_schema(
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
                                            # Restore HTML tags from placeholders for each translation
                                            return _restore_html_tags_in_translations(translations)
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
                                            translation_pattern = r'\{"message_id":\s*"([^"]+)",\s*"translated_text":\s*"([^"]*)"\}'
                                            matches = re.findall(translation_pattern, result_text)
                                            if matches:
                                                partial_translations = [{"message_id": mid, "translated_text": text} for mid, text in matches]
                                                logger.info(f"Extracted {len(partial_translations)} partial translations from truncated response")
                                                return _restore_html_tags_in_translations(partial_translations)
                                        
                                        # Try to extract JSON from markdown code blocks if present
                                        json_match = re.search(r'```(?:json)?\s*(\{.*\})\s*```', result_text, re.DOTALL)
                                        if json_match:
                                            try:
                                                result = json_lib.loads(json_match.group(1))
                                                translations = result.get("translations", [])
                                                if isinstance(translations, list):
                                                    return _restore_html_tags_in_translations(translations)
                                            except json_lib.JSONDecodeError:
                                                pass
                                        # Try to find JSON object in the text (more lenient)
                                        json_match = re.search(r'\{[^{}]*"translations"[^{}]*\[.*?\]\s*\}', result_text, re.DOTALL)
                                        if json_match:
                                            try:
                                                result = json_lib.loads(json_match.group(0))
                                                translations = result.get("translations", [])
                                                if isinstance(translations, list):
                                                    return _restore_html_tags_in_translations(translations)
                                            except json_lib.JSONDecodeError:
                                                pass
                                        
                                        logger.error(f"Failed to parse translation response. Returning empty translations.")
                                        return []
                                
                                return []
                            except Exception as e:
                                logger.error(f"Error translating batch: {e}")
                                return []
                        
                        # Translate all batches
                        # Process each batch individually and continue even if some batches fail
                        # This ensures successful translations are saved to cache even if later batches fail
                        all_new_translations: list[dict[str, str]] = []
                        batch_errors: list[str] = []
                        
                        for batch_idx, batch in enumerate(batches):
                            try:
                                batch_translations = agent.execute(_translate_batch(batch), timeout=60.0)
                                
                                # Collect translations for this batch
                                batch_translation_dict: dict[str, str] = {}
                                
                                # Save translations to MySQL immediately as they are received
                                try:
                                    from translation_cache import save_translation
                                    for translation in batch_translations:
                                        message_id = translation.get("message_id")
                                        translated_text = translation.get("translated_text", "")
                                        if message_id and translated_text:
                                            original_text = message_id_to_text.get(message_id)
                                            if original_text:
                                                save_translation(original_text, translated_text)
                                except Exception as save_error:
                                    logger.warning(f"Failed to save translations to MySQL for batch {batch_idx + 1}: {save_error}")
                                
                                for translation in batch_translations:
                                    message_id = translation.get("message_id")
                                    translated_text = translation.get("translated_text", "")
                                    if message_id and translated_text:
                                        # Find the original text for this message_id using the reverse mapping
                                        original_text = message_id_to_text.get(message_id)
                                        
                                        if original_text:
                                            # Translated text already has HTML tags restored
                                            # Update batch_translation_dict for all message_ids with this text
                                            for msg_id in text_to_message_ids.get(original_text, []):
                                                batch_translation_dict[msg_id] = translated_text
                                
                                # Stream this batch's translations to client
                                if batch_translation_dict:
                                    yield f"data: {json_lib.dumps({'type': 'translation', 'translations': batch_translation_dict})}\n\n"
                                
                                all_new_translations.extend(batch_translations)
                            except RuntimeError as e:
                                error_msg = str(e).lower()
                                if "not authenticated" in error_msg or "not running" in error_msg:
                                    # Critical error - agent is not available, send error event and stop
                                    logger.warning(f"Agent {agent_config_name} client loop issue: {e}")
                                    # Translations are already saved incrementally as batches are processed
                                    yield f"data: {json_lib.dumps({'type': 'error', 'error': 'Agent client loop is not available'})}\n\n"
                                    return
                                else:
                                    # Non-critical runtime error - log and continue with other batches
                                    error_msg_str = f"Batch {batch_idx + 1}/{len(batches)} failed: {e}"
                                    logger.error(error_msg_str)
                                    batch_errors.append(error_msg_str)
                            except TimeoutError:
                                # Timeout for this batch - log and continue with other batches
                                error_msg_str = f"Batch {batch_idx + 1}/{len(batches)} timed out"
                                logger.warning(error_msg_str)
                                batch_errors.append(error_msg_str)
                            except Exception as e:
                                # Other errors for this batch - log and continue with other batches
                                error_msg_str = f"Batch {batch_idx + 1}/{len(batches)} error: {e}"
                                logger.error(error_msg_str)
                                batch_errors.append(error_msg_str)
                        
                        # Translations are saved incrementally as batches are processed
                        # Log warning if some batches failed (but we still saved successful translations)
                        if batch_errors:
                            logger.warning(
                                f"Some translation batches failed for {agent_config_name}/{user_id}, "
                                f"but {len(all_new_translations)} successful translations were saved to cache. "
                                f"Errors: {', '.join(batch_errors)}"
                            )
                    
                    # Send completion event
                    yield f"data: {json_lib.dumps({'type': 'complete'})}\n\n"
                    
                except Exception as e:
                    logger.error(f"Error in translation stream for {agent_config_name}/{user_id}: {e}")
                    yield f"data: {json_lib.dumps({'type': 'error', 'error': str(e)})}\n\n"

            # Return streaming response with SSE content type
            return Response(
                stream_with_context(generate_translations()),
                mimetype='text/event-stream',
                headers={
                    'Cache-Control': 'no-cache',
                    'X-Accel-Buffering': 'no',  # Disable buffering in nginx
                }
            )
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

    @agents_bp.route("/api/agents/<agent_config_name>/conversation/<user_id>/summarize", methods=["POST"])
    def api_trigger_summarization(agent_config_name: str, user_id: str):
        """Trigger summarization for a conversation directly without going through the task graph."""
        try:
            agent = get_agent_by_name(agent_config_name)
            if not agent:
                return jsonify({"error": f"Agent '{agent_config_name}' not found"}), 404

            if not agent.agent_id:
                return jsonify({"error": "Agent not authenticated"}), 400

            if not agent.client or not agent.client.is_connected():
                return jsonify({"error": "Agent client not connected"}), 503

            # Trigger summarization directly (without going through task graph)
            # This is async, so we need to run it on the agent's event loop
            async def _trigger_summarize():
                channel_id = await _resolve_user_id_to_channel_id(agent, user_id)
                await trigger_summarization_directly(agent, channel_id, parse_llm_reply_fn=parse_llm_reply)

            # Use agent.execute() to run the coroutine on the agent's event loop
            try:
                agent.execute(_trigger_summarize(), timeout=60.0)  # Increased timeout for summarization
                return jsonify({"success": True, "message": "Summarization completed successfully"})
            except ValueError as e:
                return jsonify({"error": str(e)}), 400
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
                
                # Resolve user_id (which may be a username) to channel_id
                resolved_channel_id = await _resolve_user_id_to_channel_id(agent, user_id)
                
                # Get entity using agent's client to determine type
                entity_from_agent = await agent_client.get_entity(resolved_channel_id)
                
                # Import is_dm to check if this is a DM
                from telegram_util import is_dm
                
                is_direct_message = is_dm(entity_from_agent)
                return is_direct_message, entity_from_agent

            # Check if DM or group (runs on agent's event loop, but quickly)
            try:
                is_direct_message, entity_from_agent = agent.execute(_check_if_dm(), timeout=10.0)
                # Extract channel_id from entity for later use
                channel_id = getattr(entity_from_agent, 'id', None)
                if channel_id is None:
                    return jsonify({"error": "Could not determine channel ID from entity"}), 500
            except ValueError as e:
                return jsonify({"error": str(e)}), 400
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
