# src/admin_console/agents/conversation_translate.py
#
# Copyright (c) 2025-2026 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.
#
import copy
import json as json_lib
import logging
import re

from flask import Blueprint, Response, jsonify, request, stream_with_context  # pyright: ignore[reportMissingImports]

from admin_console.helpers import get_agent_by_name
from llm.factory import create_llm_from_name
from llm.exceptions import RetryableLLMError

# Import placeholder functions from conversation module
from admin_console.agents.conversation import (
    replace_html_tags_with_placeholders,
    restore_html_tags_from_placeholders,
)

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


def register_conversation_translate_routes(agents_bp: Blueprint):
    """Register conversation translation route."""

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
                        from config import TRANSLATION_MODEL
                        if not TRANSLATION_MODEL:
                            raise ValueError(
                                "TRANSLATION_MODEL environment variable is required for translation. "
                                "Set TRANSLATION_MODEL to specify the model for translations."
                            )
                        logger.info(
                            "Conversation translate using model: %s",
                            TRANSLATION_MODEL,
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
                                    agent_name="admin-translation",
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
