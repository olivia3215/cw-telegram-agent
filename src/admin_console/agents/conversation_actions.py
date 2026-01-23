# admin_console/agents/conversation_actions.py
#
# Conversation action routes for the admin console (translate, xsend, summarize, delete-telepathic-messages).

import asyncio
import base64
import copy
import html
import io
import json as json_lib
import logging
import os
import re
import tempfile
import zipfile
from datetime import datetime, timedelta
from pathlib import Path

from flask import Blueprint, jsonify, request, Response, stream_with_context  # pyright: ignore[reportMissingImports]

from admin_console.helpers import get_agent_by_name
from config import STATE_DIRECTORY, TRANSLATION_MODEL
from handlers.received import parse_llm_reply
from llm.factory import create_llm_from_name
from llm.exceptions import RetryableLLMError
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
                            except RetryableLLMError as e:
                                # Check if this is a PROHIBITED_CONTENT error
                                error_msg = str(e).lower()
                                if "prohibited_content" in error_msg or "prompt blocked" in error_msg:
                                    # Re-raise to allow retry with batch size 1
                                    raise
                                # For other retryable errors, log and return empty
                                logger.error(f"Error translating batch: {e}")
                                return []
                            except Exception as e:
                                logger.error(f"Error translating batch: {e}")
                                return []
                        
                        # Helper function to process a batch of translations
                        def _process_batch_translations(
                            batch_translations: list[dict[str, str]],
                            batch_label: str
                        ) -> tuple[dict[str, str], list[dict[str, str]]]:
                            """
                            Process translations from a batch: save to cache, build translation dict, return results.
                            
                            Returns:
                                tuple: (batch_translation_dict, list of translations)
                            """
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
                                logger.warning(f"Failed to save translations to MySQL for {batch_label}: {save_error}")
                            
                            # Build translation dict for all message IDs with the same text
                            for translation in batch_translations:
                                message_id = translation.get("message_id")
                                translated_text = translation.get("translated_text", "")
                                if message_id and translated_text:
                                    original_text = message_id_to_text.get(message_id)
                                    if original_text:
                                        # Translated text already has HTML tags restored
                                        # Update batch_translation_dict for all message_ids with this text
                                        for msg_id in text_to_message_ids.get(original_text, []):
                                            batch_translation_dict[msg_id] = translated_text
                            
                            return batch_translation_dict, batch_translations
                        
                        # Translate all batches
                        # Process each batch individually and continue even if some batches fail
                        # This ensures successful translations are saved to cache even if later batches fail
                        all_new_translations: list[dict[str, str]] = []
                        batch_errors: list[str] = []
                        
                        for batch_idx, batch in enumerate(batches):
                            try:
                                batch_translations = agent.execute(_translate_batch(batch), timeout=60.0)
                                
                                batch_translation_dict, _ = _process_batch_translations(
                                    batch_translations, f"batch {batch_idx + 1}/{len(batches)}"
                                )
                                
                                # Stream this batch's translations to client
                                if batch_translation_dict:
                                    yield f"data: {json_lib.dumps({'type': 'translation', 'translations': batch_translation_dict})}\n\n"
                                
                                all_new_translations.extend(batch_translations)
                            except RetryableLLMError as e:
                                # Check if this is a PROHIBITED_CONTENT error
                                error_msg = str(e).lower()
                                if "prohibited_content" in error_msg or "prompt blocked" in error_msg:
                                    # Retry with batch size 1 (translate messages one by one)
                                    logger.warning(f"Batch {batch_idx + 1}/{len(batches)} blocked due to PROHIBITED_CONTENT, retrying with batch size 1")
                                    batch_translation_dict: dict[str, str] = {}
                                    
                                    # Translate each message individually using the same processing logic
                                    for msg in batch:
                                        try:
                                            single_msg_batch = [msg]
                                            single_translations = agent.execute(_translate_batch(single_msg_batch), timeout=60.0)
                                            
                                            single_dict, _ = _process_batch_translations(
                                                single_translations, f"message {msg.get('message_id')}"
                                            )
                                            
                                            # Merge into batch dict
                                            batch_translation_dict.update(single_dict)
                                            all_new_translations.extend(single_translations)
                                        except RetryableLLMError as single_retry_error:
                                            # Even individual messages can be blocked - log and skip
                                            error_msg = str(single_retry_error).lower()
                                            if "prohibited_content" in error_msg or "prompt blocked" in error_msg:
                                                logger.warning(f"Individual message {msg.get('message_id')} also blocked due to PROHIBITED_CONTENT, skipping")
                                                batch_errors.append(f"Message {msg.get('message_id')} blocked: PROHIBITED_CONTENT")
                                            else:
                                                logger.error(f"Error translating individual message {msg.get('message_id')}: {single_retry_error}")
                                                batch_errors.append(f"Message {msg.get('message_id')} failed: {single_retry_error}")
                                        except Exception as single_error:
                                            # Log error for individual message but continue with others
                                            logger.error(f"Error translating individual message {msg.get('message_id')}: {single_error}")
                                            batch_errors.append(f"Message {msg.get('message_id')} failed: {single_error}")
                                    
                                    # Stream translations for this batch (now processed individually)
                                    if batch_translation_dict:
                                        yield f"data: {json_lib.dumps({'type': 'translation', 'translations': batch_translation_dict})}\n\n"
                                else:
                                    # Other retryable errors - log and continue with other batches
                                    error_msg_str = f"Batch {batch_idx + 1}/{len(batches)} failed with retryable error: {e}"
                                    logger.error(error_msg_str)
                                    batch_errors.append(error_msg_str)
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

            data = request.json
            intent = data.get("intent", "").strip()

            # Get work queue singleton
            state_path = os.path.join(STATE_DIRECTORY, "work_queue.json")
            work_queue = WorkQueue.get_instance()

            # Create xsend task by inserting a received task with xsend_intent
            # This is async, so we need to run it on the agent's event loop
            async def _create_xsend():
                # Resolve user_id to channel_id (handles @username, phone numbers, and numeric IDs)
                channel_id = await _resolve_user_id_to_channel_id(agent, user_id)
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
            except ValueError as e:
                return jsonify({"error": str(e)}), 400
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
                from utils.telegram import is_dm
                
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

    @agents_bp.route("/api/agents/<agent_config_name>/conversation/<user_id>/download", methods=["POST"])
    def api_download_conversation(agent_config_name: str, user_id: str):
        """Download full conversation (up to 2500 messages) as a zip file with standalone HTML."""
        try:
            agent = get_agent_by_name(agent_config_name)
            if not agent:
                return jsonify({"error": f"Agent '{agent_config_name}' not found"}), 404

            # Get include_translations from request body
            data = request.json or {}
            include_translations = data.get("include_translations", False)

            if not agent.client or not agent.client.is_connected():
                return jsonify({"error": "Agent client not connected"}), 503

            # Resolve user_id to channel_id
            from admin_console.helpers import resolve_user_id_and_handle_errors
            channel_id, error_response = resolve_user_id_and_handle_errors(agent, user_id, logger)
            if error_response:
                return error_response

            # Check if agent's event loop is accessible
            try:
                client_loop = agent._get_client_loop()
                if not client_loop or not client_loop.is_running():
                    raise RuntimeError("Agent client event loop is not accessible or not running")
            except Exception as e:
                logger.warning(f"Cannot download conversation - event loop check failed: {e}")
                return jsonify({"error": "Agent client event loop is not available"}), 503

            # Import necessary modules for message fetching and formatting
            from admin_console.agents.conversation_get import (
                _replace_custom_emojis_with_images,
                _replace_custom_emoji_in_reactions,
            )
            from handlers.received_helpers.message_processing import format_message_reactions
            from media.media_injector import format_message_for_prompt, inject_media_descriptions
            from media.media_source import get_default_media_source_chain
            from utils.telegram import get_channel_name, is_dm
            from telethon.tl.functions.messages import GetPeerDialogsRequest
            from telethon.tl.functions.stories import GetStoriesByIDRequest
            from telegram_download import download_media_bytes
            from telegram_media import iter_media_parts, get_unique_id
            from media.mime_utils import detect_mime_type_from_bytes, get_file_extension_from_mime_or_bytes
            from config import CONFIG_DIRECTORIES
            import glob
            import shutil

            # This is async, so we need to run it in the client's event loop
            async def _download_conversation():
                try:
                    client = agent.client
                    entity = await client.get_entity(channel_id)
                    if not entity:
                        raise ValueError(f"Cannot resolve entity for channel_id {channel_id}")

                    # Get summaries
                    from db import summaries as db_summaries
                    summaries = db_summaries.load_summaries(agent.agent_id, channel_id)
                    summaries.sort(key=lambda x: (x.get("min_message_id", 0), x.get("max_message_id", 0)))

                    # Fetch ALL messages (up to 2500) - not just unsummarized
                    # Don't use min_id filter - we want everything
                    messages = []
                    total_fetched = 0
                    async for message in client.iter_messages(entity, limit=2500):
                        total_fetched += 1
                        if total_fetched >= 2500:
                            break

                        # Extract message data similar to conversation_get.py
                        msg_id = int(message.id)
                        
                        # Get sender info
                        from_id = getattr(message, "from_id", None)
                        sender_id = None
                        if from_id:
                            sender_id = getattr(from_id, "user_id", None) or getattr(from_id, "channel_id", None)
                        
                        if not sender_id:
                            sender = getattr(message, "sender", None)
                            if sender:
                                sender_id = getattr(sender, "id", None)
                        
                        is_from_agent = sender_id == agent.agent_id
                        
                        sender_name = None
                        if sender_id and isinstance(sender_id, int):
                            try:
                                sender_name = await get_channel_name(agent, sender_id)
                                if not sender_name or not sender_name.strip():
                                    sender_name = "User"
                            except Exception:
                                sender_name = "User"
                        elif sender_id:
                            sender_name = "User"
                        else:
                            sender_name = "User"
                        
                        sender_name = html.escape(sender_name)
                        
                        timestamp = message.date.isoformat() if hasattr(message, "date") and message.date else None
                        
                        # Extract reply_to
                        reply_to_msg_id = None
                        reply_to = getattr(message, "reply_to", None)
                        if reply_to:
                            reply_to_msg_id_val = getattr(reply_to, "reply_to_msg_id", None)
                            if reply_to_msg_id_val is not None:
                                reply_to_msg_id = str(reply_to_msg_id_val)
                        
                        # Format reactions
                        reactions_str = await format_message_reactions(agent, message)
                        if reactions_str:
                            reactions_str = await _replace_custom_emoji_in_reactions(
                                reactions_str, agent_config_name, str(message.id), message, agent
                            )
                        
                        # Format media/stickers
                        media_chain = get_default_media_source_chain()
                        message_parts = await format_message_for_prompt(message, agent=agent, media_chain=media_chain)
                        
                        # Get text with formatting
                        text_markdown = getattr(message, "text_markdown", None)
                        raw_text = getattr(message, "message", None) or getattr(message, "text", None) or ""
                        entities = getattr(message, "entities", None) or []
                        
                        if not text_markdown or text_markdown == raw_text:
                            if raw_text and entities:
                                from utils.telegram_entities import entities_to_markdown
                                text_markdown = entities_to_markdown(raw_text, entities)
                            else:
                                text_markdown = raw_text
                        
                        # Convert markdown to HTML
                        text = markdown_to_html(text_markdown)
                        
                        # Replace custom emojis with images
                        text = await _replace_custom_emojis_with_images(
                            text, raw_text, entities, agent_config_name, str(message.id), message
                        )
                        
                        # Build message parts
                        parts = []
                        for part in message_parts:
                            if part.get("kind") == "text":
                                part_text = part.get("text", "")
                                part_html = markdown_to_html(part_text)
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
                                    "is_animated": part.get("is_animated", False),
                                    "message_id": str(message.id),
                                })
                        
                        # If text is empty but we have parts, extract from first text part
                        if not text and parts:
                            for part in parts:
                                if part.get("kind") == "text":
                                    part_text = part.get("text", "")
                                    if part_text:
                                        text = part_text
                                        break
                        
                        # Ensure message has content
                        if not parts and not text:
                            parts.append({
                                "kind": "text",
                                "text": "[Message]"
                            })
                            text = "[Message]"
                        
                        messages.append({
                            "id": str(message.id),
                            "text": text,
                            "parts": parts,
                            "sender_id": str(sender_id) if sender_id else None,
                            "sender_name": sender_name,
                            "is_from_agent": is_from_agent,
                            "timestamp": timestamp,
                            "reply_to_msg_id": reply_to_msg_id,
                            "reactions": reactions_str,
                        })
                    
                    # Reverse to chronological order
                    messages = list(reversed(messages))
                    
                    logger.info(
                        f"[{agent_config_name}] Fetched {len(messages)} messages for download (channel {channel_id})"
                    )
                    
                    # Fetch translations if requested
                    translations = {}
                    if include_translations:
                        # Get messages with text that need translation
                        messages_to_translate = []
                        for msg in messages:
                            msg_id = str(msg.get("id", ""))
                            msg_text = msg.get("text", "")
                            if not msg_text:
                                continue
                            
                            # Check cache first
                            from translation_cache import get_translation
                            cached_translation = get_translation(msg_text)
                            if cached_translation:
                                translations[msg_id] = cached_translation
                            else:
                                messages_to_translate.append({
                                    "message_id": msg_id,
                                    "text": msg_text
                                })
                        
                        # Translate remaining messages in batches
                        if messages_to_translate:
                            if not TRANSLATION_MODEL:
                                logger.warning("TRANSLATION_MODEL not set, skipping translations")
                            else:
                                translation_llm = create_llm_from_name(TRANSLATION_MODEL)
                                batch_size = 10
                                batches = [
                                    messages_to_translate[i:i + batch_size]
                                    for i in range(0, len(messages_to_translate), batch_size)
                                ]
                                
                                for batch in batches:
                                    # Replace HTML tags with placeholders
                                    batch_with_placeholders = []
                                    batch_tag_maps = {}
                                    
                                    for msg in batch:
                                        message_id = msg["message_id"]
                                        html_text = msg["text"]
                                        text_with_placeholders, tag_map = replace_html_tags_with_placeholders(html_text)
                                        batch_tag_maps[message_id] = tag_map
                                        batch_with_placeholders.append({
                                            "message_id": message_id,
                                            "text": text_with_placeholders
                                        })
                                    
                                    # Translate batch
                                    messages_json = json_lib.dumps(batch_with_placeholders, ensure_ascii=False, indent=2)
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
                                    
                                    system_prompt = (
                                        "You are a translation assistant. Translate messages into English and return JSON.\n\n"
                                        f"{translation_prompt}"
                                    )
                                    
                                    result_text = await translation_llm.query_with_json_schema(
                                        system_prompt=system_prompt,
                                        json_schema=copy.deepcopy(_TRANSLATION_SCHEMA),
                                        model=None,
                                        timeout_s=None,
                                    )
                                    
                                    if result_text:
                                        try:
                                            result = json_lib.loads(result_text)
                                            batch_translations = result.get("translations", [])
                                            for translation in batch_translations:
                                                message_id = translation.get("message_id")
                                                translated_text = translation.get("translated_text", "")
                                                if message_id and message_id in batch_tag_maps:
                                                    tag_map = batch_tag_maps[message_id]
                                                    restored_text = restore_html_tags_from_placeholders(
                                                        translated_text, tag_map
                                                    )
                                                    translations[message_id] = restored_text
                                        except Exception as e:
                                            logger.warning(f"Error parsing translation batch: {e}")
                    
                    # Create temp directory for zip contents
                    with tempfile.TemporaryDirectory() as temp_dir:
                        temp_path = Path(temp_dir)
                        media_dir = temp_path / "media"
                        media_dir.mkdir()
                        
                        # Download all media files
                        media_map = {}  # unique_id -> filename
                        for msg in messages:
                            for part in msg.get("parts", []):
                                if part.get("kind") == "media":
                                    unique_id = part.get("unique_id")
                                    message_id = part.get("message_id")
                                    if not unique_id or unique_id in media_map:
                                        continue
                                    
                                    # Try to find cached media first
                                    cached_file = None
                                    escaped_unique_id = glob.escape(unique_id)
                                    
                                    for config_dir in CONFIG_DIRECTORIES:
                                        config_media_dir = Path(config_dir) / "media"
                                        if config_media_dir.exists():
                                            for file_path in config_media_dir.glob(f"{escaped_unique_id}.*"):
                                                if file_path.suffix.lower() != ".json":
                                                    cached_file = file_path
                                                    break
                                            if cached_file:
                                                break
                                    
                                    if not cached_file:
                                        state_media_dir = Path(STATE_DIRECTORY) / "media"
                                        if state_media_dir.exists():
                                            for file_path in state_media_dir.glob(f"{escaped_unique_id}.*"):
                                                if file_path.suffix.lower() != ".json":
                                                    cached_file = file_path
                                                    break
                                    
                                    # If found in cache, copy it
                                    if cached_file and cached_file.exists():
                                        try:
                                            with open(cached_file, "rb") as f:
                                                media_bytes = f.read()
                                            mime_type = detect_mime_type_from_bytes(media_bytes[:1024])
                                            ext = get_file_extension_from_mime_or_bytes(mime_type, media_bytes)
                                            filename = f"{unique_id}{ext}"
                                            media_path = media_dir / filename
                                            with open(media_path, "wb") as f:
                                                f.write(media_bytes)
                                            media_map[unique_id] = filename
                                        except Exception as e:
                                            logger.warning(f"Error copying cached media {unique_id}: {e}")
                                    else:
                                        # Download from Telegram
                                        try:
                                            msg_id_int = int(message_id)
                                            msg_obj = await client.get_messages(entity, ids=msg_id_int)
                                            if isinstance(msg_obj, list):
                                                if len(msg_obj) > 0:
                                                    msg_obj = msg_obj[0]
                                                else:
                                                    continue
                                            
                                            media_items = iter_media_parts(msg_obj)
                                            for item in media_items:
                                                if item.unique_id == unique_id:
                                                    media_bytes = await download_media_bytes(client, item.file_ref)
                                                    mime_type = detect_mime_type_from_bytes(media_bytes[:1024])
                                                    ext = get_file_extension_from_mime_or_bytes(mime_type, media_bytes)
                                                    filename = f"{unique_id}{ext}"
                                                    media_path = media_dir / filename
                                                    with open(media_path, "wb") as f:
                                                        f.write(media_bytes)
                                                    media_map[unique_id] = filename
                                                    break
                                        except Exception as e:
                                            logger.warning(f"Error downloading media {unique_id}: {e}")
                        
                        # Download custom emoji files
                        emoji_map = {}  # document_id -> filename
                        import re
                        emoji_pattern = r'data-document-id="(\d+)"'
                        
                        # Collect all emoji document IDs from messages and reactions
                        emoji_doc_ids = set()
                        for msg in messages:
                            # Extract emoji document IDs from message text (custom-emoji-container tags)
                            for match in re.finditer(emoji_pattern, msg.get("text", "")):
                                doc_id = int(match.group(1))
                                emoji_doc_ids.add(doc_id)
                            
                            # Also extract emoji document IDs from reactions (custom-emoji-reaction img tags)
                            reactions_str = msg.get("reactions", "")
                            if reactions_str:
                                for match in re.finditer(emoji_pattern, reactions_str):
                                    doc_id = int(match.group(1))
                                    emoji_doc_ids.add(doc_id)
                        
                        # Download each unique emoji
                        for doc_id in emoji_doc_ids:
                            if doc_id in emoji_map:
                                continue
                            
                            # Try to get emoji from cache
                            try:
                                from telethon.tl.functions.messages import GetCustomEmojiDocumentsRequest
                                result = await client(GetCustomEmojiDocumentsRequest(document_id=[doc_id]))
                                
                                documents = None
                                if hasattr(result, "documents"):
                                    documents = result.documents
                                elif hasattr(result, "document"):
                                    documents = [result.document] if result.document else []
                                elif isinstance(result, list):
                                    documents = result
                                
                                if documents and len(documents) > 0:
                                    doc = documents[0]
                                    unique_id_emoji = get_unique_id(doc)
                                    if unique_id_emoji:
                                        # Check cache for emoji
                                        cached_emoji = None
                                        escaped_uid = glob.escape(unique_id_emoji)
                                        for config_dir in CONFIG_DIRECTORIES:
                                            config_media_dir = Path(config_dir) / "media"
                                            if config_media_dir.exists():
                                                for file_path in config_media_dir.glob(f"{escaped_uid}.*"):
                                                    if file_path.suffix.lower() != ".json":
                                                        cached_emoji = file_path
                                                        break
                                                if cached_emoji:
                                                    break
                                        
                                        if not cached_emoji:
                                            state_media_dir = Path(STATE_DIRECTORY) / "media"
                                            if state_media_dir.exists():
                                                for file_path in state_media_dir.glob(f"{escaped_uid}.*"):
                                                    if file_path.suffix.lower() != ".json":
                                                        cached_emoji = file_path
                                                        break
                                        
                                        if cached_emoji and cached_emoji.exists():
                                            with open(cached_emoji, "rb") as f:
                                                emoji_bytes = f.read()
                                            mime_type = detect_mime_type_from_bytes(emoji_bytes[:1024])
                                            ext = get_file_extension_from_mime_or_bytes(mime_type, emoji_bytes)
                                            filename = f"emoji_{doc_id}{ext}"
                                            emoji_path = media_dir / filename
                                            with open(emoji_path, "wb") as f:
                                                f.write(emoji_bytes)
                                            emoji_map[doc_id] = filename
                                        else:
                                            # Download emoji
                                            emoji_bytes = await download_media_bytes(client, doc)
                                            mime_type = detect_mime_type_from_bytes(emoji_bytes[:1024])
                                            ext = get_file_extension_from_mime_or_bytes(mime_type, emoji_bytes)
                                            filename = f"emoji_{doc_id}{ext}"
                                            emoji_path = media_dir / filename
                                            with open(emoji_path, "wb") as f:
                                                f.write(emoji_bytes)
                                            emoji_map[doc_id] = filename
                            except Exception as e:
                                logger.warning(f"Error downloading emoji {doc_id}: {e}")
                        
                        # Generate HTML
                        agent_tz_id = agent.get_timezone_identifier()
                        html_content = _generate_standalone_html(
                            agent_config_name, user_id, summaries, messages, translations, 
                            agent_tz_id, media_map, emoji_map, include_translations
                        )
                        
                        # Write HTML
                        html_path = temp_path / "index.html"
                        with open(html_path, "w", encoding="utf-8") as f:
                            f.write(html_content)
                        
                        # Create zip file
                        zip_buffer = io.BytesIO()
                        with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
                            zip_file.write(html_path, "index.html")
                            for media_file in media_dir.iterdir():
                                zip_file.write(media_file, f"media/{media_file.name}")
                        
                        zip_buffer.seek(0)
                        return zip_buffer.getvalue()
                
                except Exception as e:
                    logger.error(f"Error downloading conversation: {e}", exc_info=True)
                    raise
            
            # Execute async function
            try:
                zip_data = agent.execute(_download_conversation(), timeout=300.0)  # 5 minute timeout
                
                # Return zip file as download
                from datetime import datetime
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                filename = f"conversation_{agent_config_name}_{user_id}_{timestamp}.zip"
                
                return Response(
                    zip_data,
                    mimetype="application/zip",
                    headers={
                        "Content-Disposition": f"attachment; filename={filename}",
                        "Content-Length": str(len(zip_data))
                    }
                )
            except RuntimeError as e:
                error_msg = str(e).lower()
                if "not authenticated" in error_msg or "not running" in error_msg:
                    logger.warning(f"Agent {agent_config_name} client loop issue: {e}")
                    return jsonify({"error": "Agent client loop is not available"}), 503
                else:
                    logger.error(f"Error downloading conversation: {e}")
                    return jsonify({"error": str(e)}), 500
            except TimeoutError:
                logger.warning(f"Timeout downloading conversation for agent {agent_config_name}, user {user_id}")
                return jsonify({"error": "Timeout downloading conversation"}), 504
        except Exception as e:
            logger.error(f"Error downloading conversation for {agent_config_name}/{user_id}: {e}", exc_info=True)
            return jsonify({"error": str(e)}), 500


def _generate_standalone_html(
    agent_name: str, user_id: str, summaries: list, messages: list, 
    translations: dict, agent_timezone: str, media_map: dict, emoji_map: dict,
    show_translations: bool
) -> str:
    """Generate standalone HTML file for conversation display."""
    # This will be a large HTML string with embedded CSS and JavaScript
    # Similar to the renderConversation function in admin_console.html
    
    # Get Lottie and pako from CDN (same as admin console)
    html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Conversation: {html.escape(agent_name)} / {html.escape(user_id)}</title>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/lottie-web/5.12.2/lottie.min.js"></script>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/pako/2.1.0/pako.min.js"></script>
    <style>
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            margin: 0;
            padding: 16px;
            background-color: #f5f5f5;
            max-width: 1200px;
            margin: 0 auto;
        }}
        .message {{
            background: white;
            padding: 12px;
            margin-bottom: 8px;
            border-radius: 8px;
            box-shadow: 0 1px 2px rgba(0,0,0,0.1);
            border-left: 4px solid #4caf50;
        }}
        .message.agent {{
            background: #e3f2fd;
            border-left-color: #2196f3;
        }}
        .message-header {{
            font-size: 12px;
            color: #666;
            margin-bottom: 4px;
        }}
        .message-content {{
            white-space: pre-wrap;
            margin-bottom: 4px;
        }}
        .message-media {{
            margin: 8px 0;
        }}
        .message-media img {{
            max-width: 300px;
            max-height: 300px;
            border-radius: 8px;
        }}
        .message-media video {{
            max-width: 300px;
            max-height: 300px;
            border-radius: 8px;
        }}
        .message-media audio {{
            width: 100%;
            max-width: 400px;
        }}
        .tgs-container {{
            width: 200px;
            height: 200px;
            position: relative;
        }}
        .summary {{
            background: white;
            padding: 16px;
            margin-bottom: 16px;
            border-radius: 8px;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
        }}
        .summary-header {{
            font-weight: bold;
            margin-bottom: 8px;
        }}
        .reactions {{
            font-size: 11px;
            color: #888;
            margin-top: 4px;
            font-style: italic;
        }}
        .custom-emoji-container {{
            display: inline-block;
        }}
        .custom-emoji-img {{
            width: 1.2em;
            height: 1.2em;
            vertical-align: middle;
        }}
    </style>
</head>
<body>
    <h1>Conversation: {html.escape(agent_name)} / {html.escape(user_id)}</h1>
"""
    
    # Add summaries
    if summaries:
        html_content += '<div style="margin-bottom: 24px;"><h2>Conversation Summaries</h2>\n'
        for summary in summaries:
            html_content += f"""    <div class="summary">
        <div class="summary-header">ID: {html.escape(str(summary.get('id', 'N/A')))} | Created: {html.escape(str(summary.get('created', 'N/A')))}</div>
        <div>Message IDs: {html.escape(str(summary.get('min_message_id', '')))} - {html.escape(str(summary.get('max_message_id', '')))}</div>
        <div>Dates: {html.escape(str(summary.get('first_message_date', '')))} to {html.escape(str(summary.get('last_message_date', '')))}</div>
        <div>{html.escape(summary.get('content', ''))}</div>
    </div>
"""
        html_content += '</div>\n'
    
    # Add messages
    html_content += '<h2>Messages</h2>\n'
    for msg in messages:
        msg_id = str(msg.get("id", ""))
        is_from_agent = msg.get("is_from_agent", False)
        sender_name = msg.get("sender_name", "User")
        sender_id = msg.get("sender_id")
        timestamp = msg.get("timestamp", "")
        reply_to = msg.get("reply_to_msg_id")
        reactions = msg.get("reactions", "")
        
        # Format timestamp (simplified - just show the ISO timestamp)
        # The timestamp is already in ISO format from the backend
        if timestamp:
            try:
                # Parse and format nicely
                from datetime import datetime
                dt = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
                formatted_time = dt.strftime("%Y-%m-%d %H:%M:%S")
            except:
                formatted_time = timestamp
        else:
            formatted_time = "N/A"
        
        # Get text (translated if requested and available)
        text = msg.get("text", "")
        if show_translations and msg_id in translations:
            text = translations[msg_id]
        
        # Replace emoji URLs with local paths
        text = _replace_emoji_urls_with_local(text, emoji_map)
        
        # Build content from parts
        content_html = ""
        parts = msg.get("parts", [])
        if parts:
            for part in parts:
                if part.get("kind") == "text":
                    part_text = part.get("text", "")
                    if show_translations and msg_id in translations:
                        # For parts, we'd need part-level translations, but for now use message-level
                        part_text = translations.get(msg_id, part_text)
                    part_text = _replace_emoji_urls_with_local(part_text, emoji_map)
                    content_html += f'<div class="message-content">{part_text}</div>\n'
                elif part.get("kind") == "media":
                    unique_id = part.get("unique_id")
                    media_kind = part.get("media_kind", "media")
                    is_animated = part.get("is_animated", False) or media_kind == "animated_sticker"
                    
                    if unique_id in media_map:
                        media_path = f"media/{media_map[unique_id]}"
                        
                        if media_kind == "photo" or (media_kind == "sticker" and not is_animated):
                            sticker_name = part.get("sticker_name", unique_id)
                            content_html += f'<div class="message-media"><img src="{html.escape(media_path)}" alt="{html.escape(sticker_name)}"></div>\n'
                        elif is_animated:
                            # TGS animation - will be loaded by JavaScript
                            content_html += f'<div class="message-media"><div class="tgs-container" id="tgs-{unique_id}" data-unique-id="{unique_id}" data-path="{html.escape(media_path)}"></div></div>\n'
                        elif media_kind in ("video", "animation", "gif"):
                            content_html += f'<div class="message-media"><video controls autoplay loop muted><source src="{html.escape(media_path)}"></video></div>\n'
                        elif media_kind == "audio":
                            content_html += f'<div class="message-media"><audio controls><source src="{html.escape(media_path)}"></audio></div>\n'
                        else:
                            rendered_text = part.get("rendered_text", "")
                            content_html += f'<div class="message-media" style="color: #666; font-style: italic;">{html.escape(rendered_text)} <a href="{html.escape(media_path)}" download>[Download]</a></div>\n'
                        
                        if part.get("rendered_text"):
                            content_html += f'<div style="color: #666; font-size: 11px; margin-top: 2px; font-style: italic;">{html.escape(part.get("rendered_text", ""))}</div>\n'
        else:
            # Fallback to text
            if text:
                content_html = f'<div class="message-content">{text}</div>\n'
            else:
                content_html = '<div class="message-content">[No content]</div>\n'
        
        # Build metadata
        metadata = f"{html.escape(sender_name)}"
        if sender_id:
            metadata += f" ({html.escape(str(sender_id))})"
        metadata += f" • {formatted_time} • ID: {html.escape(msg_id)}"
        if reply_to:
            metadata += f" • Reply to: {html.escape(str(reply_to))}"
        
        # Build message HTML
        msg_class = "message agent" if is_from_agent else "message"
        html_content += f"""    <div class="{msg_class}" id="msg-{html.escape(msg_id)}">
        <div class="message-header">{metadata}</div>
        {content_html}
"""
        if reactions:
            # Replace emoji URLs in reactions with local paths
            reactions_local = _replace_emoji_urls_with_local(reactions, emoji_map)
            html_content += f'        <div class="reactions">Reactions: {reactions_local}</div>\n'
        html_content += "    </div>\n"
    
    # Add JavaScript for TGS animations
    html_content += """
    <script>
        // Load TGS animations
        document.addEventListener('DOMContentLoaded', function() {
            const tgsContainers = document.querySelectorAll('.tgs-container');
            tgsContainers.forEach(function(container) {
                const uniqueId = container.getAttribute('data-unique-id');
                const mediaPath = container.getAttribute('data-path');
                
                fetch(mediaPath)
                    .then(response => response.arrayBuffer())
                    .then(function(tgsData) {
                        // Decompress with pako
                        const decompressed = pako.inflate(new Uint8Array(tgsData), { to: 'string' });
                        const jsonData = JSON.parse(decompressed);
                        
                        // Initialize Lottie
                        lottie.loadAnimation({
                            container: container,
                            renderer: 'svg',
                            loop: true,
                            autoplay: true,
                            animationData: jsonData
                        });
                    })
                    .catch(function(error) {
                        console.error('Failed to load TGS animation:', error);
                        container.innerHTML = '<div style="text-align: center; color: #dc3545;">⚠️ Animation Error</div>';
                    });
            });
        });
    </script>
</body>
</html>
"""
    
    return html_content


def _replace_emoji_urls_with_local(html_text: str, emoji_map: dict) -> str:
    """Replace emoji URLs in HTML with local file paths."""
    import re
    if not html_text:
        return html_text
    
    # Replace data-emoji-url with local path
    # Pattern: data-emoji-url="/admin/api/agents/.../emoji/12345"
    pattern = r'data-emoji-url="[^"]*emoji/(\d+)"'
    def replace_emoji(match):
        doc_id = int(match.group(1))
        if doc_id in emoji_map:
            return f'src="media/{emoji_map[doc_id]}"'
        return match.group(0)
    
    html_text = re.sub(pattern, replace_emoji, html_text)
    
    # Also replace img src URLs for emojis
    # Pattern: <img ... src="/admin/api/agents/.../emoji/12345" ...>
    pattern2 = r'(<img[^>]*src=")[^"]*emoji/(\d+)"([^>]*>)'
    def replace_emoji_img(match):
        doc_id = int(match.group(2))
        if doc_id in emoji_map:
            return f'{match.group(1)}media/{emoji_map[doc_id]}"{match.group(3)}'
        return match.group(0)
    
    html_text = re.sub(pattern2, replace_emoji_img, html_text)
    
    return html_text
