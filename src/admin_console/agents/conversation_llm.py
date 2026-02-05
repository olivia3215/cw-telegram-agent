# admin_console/agents/conversation_llm.py
#
# Conversation parameters management routes for the admin console (LLM, muted, gagged).

import logging

from flask import Blueprint, jsonify, request  # pyright: ignore[reportMissingImports]

from admin_console.helpers import get_agent_by_name, get_available_llms, get_default_llm
from utils.telegram import can_agent_send_to_channel, is_dm, is_user_blocking_agent

logger = logging.getLogger(__name__)


def register_conversation_llm_routes(agents_bp: Blueprint):
    """Register conversation parameters routes (LLM, muted, gagged)."""
    
    @agents_bp.route("/api/agents/<agent_config_name>/conversation-parameters/<user_id>", methods=["GET"])
    def api_get_conversation_parameters(agent_config_name: str, user_id: str):
        """Get conversation parameters (LLM, muted, gagged) for a user."""
        try:
            agent = get_agent_by_name(agent_config_name)
            if not agent:
                return jsonify({"error": f"Agent '{agent_config_name}' not found"}), 404

            # Resolve user_id (which may be a username) to channel_id
            from admin_console.helpers import resolve_user_id_and_handle_errors
            channel_id, error_response = resolve_user_id_and_handle_errors(agent, user_id, logger)
            if error_response:
                return error_response

            # Ensure channel_id is an integer
            try:
                channel_id = int(channel_id)
            except (ValueError, TypeError):
                return jsonify({"error": "Invalid channel ID"}), 400

            if not agent.is_authenticated:
                return jsonify({"error": "Agent not authenticated"}), 503

            # Get conversation LLM from MySQL
            from db import conversation_llm as db_conversation_llm
            conversation_llm = db_conversation_llm.get_conversation_llm(agent.agent_id, channel_id)
            agent_default_llm = agent._llm_name or get_default_llm()
            available_llms = get_available_llms()

            # Mark which LLM is the agent's default
            for llm in available_llms:
                if llm["value"] == agent_default_llm:
                    llm["is_default"] = True
                else:
                    llm["is_default"] = False

            # Get muted status (Telegram notification setting)
            async def _get_muted():
                return await agent.is_muted(channel_id)
            
            try:
                is_muted = agent.execute(_get_muted(), timeout=10.0)
            except Exception as e:
                logger.warning(f"Error getting muted status: {e}")
                is_muted = False

            # Get gagged status (database override)
            from db import conversation_gagged
            gagged_override = conversation_gagged.get_conversation_gagged(agent.agent_id, channel_id)
            # If override exists, use it; otherwise use global default
            is_gagged = gagged_override if gagged_override is not None else agent.is_gagged

            async def _get_blocked_status():
                try:
                    entity = await agent.get_cached_entity(channel_id)
                    if not entity and agent.client:
                        entity = await agent.client.get_entity(channel_id)
                    is_dm_conversation = bool(entity) and is_dm(entity)
                    agent_blocked_user = False
                    user_blocked_agent = False
                    if is_dm_conversation:
                        api_cache = agent.api_cache
                        if api_cache:
                            agent_blocked_user = await api_cache.is_blocked(channel_id, ttl_seconds=0)
                        user_blocked_agent = await is_user_blocking_agent(agent, channel_id)
                        can_send = not (agent_blocked_user or user_blocked_agent)
                    else:
                        can_send = await can_agent_send_to_channel(agent, channel_id)
                    return is_dm_conversation, agent_blocked_user, user_blocked_agent, can_send
                except Exception as e:
                    logger.warning(f"Error getting block/send status: {e}")
                    return False, False, False, True

            is_dm_conversation, agent_blocked_user, user_blocked_agent, can_send = agent.execute(
                _get_blocked_status(), timeout=10.0
            )
            is_blocked = agent_blocked_user or user_blocked_agent

            return jsonify({
                "conversation_llm": conversation_llm,
                "agent_default_llm": agent_default_llm,
                "available_llms": available_llms,
                "is_muted": is_muted,
                "is_gagged": is_gagged,
                # Expose override info so the UI can distinguish "global default" from a per-conversation override.
                "gagged_override": gagged_override,
                "agent_is_gagged": agent.is_gagged,
                "is_blocked": is_blocked,
                "is_dm": is_dm_conversation,
                "agent_blocked_user": agent_blocked_user,
                "user_blocked_agent": user_blocked_agent,
                "can_send": can_send,
            })
        except Exception as e:
            logger.error(f"Error getting conversation parameters for {agent_config_name}/{user_id}: {e}")
            return jsonify({"error": str(e)}), 500

    @agents_bp.route("/api/agents/<agent_config_name>/conversation-parameters/<user_id>", methods=["PUT"])
    def api_update_conversation_parameters(agent_config_name: str, user_id: str):
        """Update conversation parameters (LLM, muted, gagged) for a user."""
        try:
            agent = get_agent_by_name(agent_config_name)
            if not agent:
                return jsonify({"error": f"Agent '{agent_config_name}' not found"}), 404

            # Resolve user_id (which may be a username) to channel_id
            from admin_console.helpers import resolve_user_id_and_handle_errors
            channel_id, error_response = resolve_user_id_and_handle_errors(agent, user_id, logger)
            if error_response:
                return error_response

            # Ensure channel_id is an integer
            try:
                channel_id = int(channel_id)
            except (ValueError, TypeError):
                return jsonify({"error": "Invalid channel ID"}), 400

            data = request.json or {}
            llm_name = data.get("llm_name")
            if llm_name is not None:
                llm_name = str(llm_name).strip() if llm_name else None
            is_muted = data.get("is_muted") if "is_muted" in data else None
            is_gagged = data.get("is_gagged") if "is_gagged" in data else None
            is_blocked = data.get("is_blocked") if "is_blocked" in data else None

            # Update in MySQL/database
            if not agent.is_authenticated:
                return jsonify({"error": "Agent not authenticated"}), 503

            has_db_changes = llm_name is not None or is_gagged is not None

            async def _set_muted_status(muted: bool) -> None:
                from admin_console.agents.memberships import _set_mute_status

                client = agent.client
                if not client or not client.is_connected():
                    raise RuntimeError("Agent client not connected")

                entity = await agent.get_cached_entity(channel_id)
                if not entity:
                    entity = await client.get_entity(channel_id)
                if entity:
                    await _set_mute_status(client, entity, muted)
                    # Invalidate cache
                    if agent.api_cache and hasattr(agent.api_cache, "_mute_cache"):
                        agent.api_cache._mute_cache.pop(channel_id, None)

            async def _set_blocked_status(blocked: bool) -> None:
                from telethon.tl.functions.contacts import BlockRequest, UnblockRequest  # pyright: ignore[reportMissingImports]

                client = agent.client
                if not client or not client.is_connected():
                    raise RuntimeError("Agent client not connected")

                entity = await agent.get_cached_entity(channel_id)
                if not entity:
                    entity = await client.get_entity(channel_id)
                if not entity or not is_dm(entity):
                    raise ValueError("Blocking is only supported for direct messages")

                input_entity = await client.get_input_entity(entity)
                if blocked:
                    await client(BlockRequest(id=input_entity))
                else:
                    await client(UnblockRequest(id=input_entity))
                if agent.api_cache:
                    agent.api_cache.invalidate_blocklist_cache()

            # If we have DB changes and Telegram changes together, we want "all-or-nothing" behavior:
            # - Apply DB changes in a transaction
            # - Apply Telegram changes
            # - Only commit DB changes if Telegram steps succeed
            #
            # This prevents the old behavior where LLM persisted even when muted failed.
            if has_db_changes:
                from db.connection import get_db_connection
                from db import conversation_gagged, conversation_llm

                with get_db_connection() as conn:
                    try:
                        # Stage DB changes (uncommitted)
                        if llm_name is not None:
                            agent_default_llm = agent._llm_name or get_default_llm()
                            conversation_llm.set_conversation_llm(
                                agent.agent_id,
                                channel_id,
                                llm_name,
                                agent_default_llm,
                                conn=conn,
                            )

                        if is_gagged is not None:
                            # If setting to global default, remove override; otherwise set override
                            if is_gagged == agent.is_gagged:
                                conversation_gagged.set_conversation_gagged(
                                    agent.agent_id, channel_id, None, conn=conn
                                )
                            else:
                                conversation_gagged.set_conversation_gagged(
                                    agent.agent_id, channel_id, bool(is_gagged), conn=conn
                                )

                        # Apply Telegram updates (external side-effect) before committing DB.
                        if is_muted is not None:
                            try:
                                agent.execute(_set_muted_status(bool(is_muted)), timeout=30.0)
                                logger.info(f"Set muted status to {is_muted} for channel {channel_id}")
                            except Exception as e:
                                # Roll back any staged DB changes so we don't partially apply.
                                conn.rollback()
                                logger.warning(f"Error setting muted status: {e}")
                                return jsonify({"error": f"Failed to set muted status: {str(e)}"}), 500
                        if is_blocked is not None:
                            try:
                                agent.execute(_set_blocked_status(bool(is_blocked)), timeout=30.0)
                                logger.info(f"Set blocked status to {is_blocked} for channel {channel_id}")
                            except ValueError as e:
                                conn.rollback()
                                return jsonify({"error": str(e)}), 400
                            except Exception as e:
                                conn.rollback()
                                logger.warning(f"Error setting blocked status: {e}")
                                return jsonify({"error": f"Failed to set blocked status: {str(e)}"}), 500

                        # All good: commit staged DB changes.
                        conn.commit()

                        if llm_name is not None:
                            agent_default_llm = agent._llm_name or get_default_llm()
                            if llm_name == agent_default_llm or not llm_name:
                                logger.info("Removed conversation LLM override (using agent default)")
                            else:
                                logger.info(f"Set conversation LLM override to '{llm_name}'")

                        if is_gagged is not None:
                            if is_gagged == agent.is_gagged:
                                logger.info(
                                    f"Removed conversation gagged override (using global default: {agent.is_gagged})"
                                )
                            else:
                                logger.info(f"Set conversation gagged override to {bool(is_gagged)}")

                    except Exception as e:
                        conn.rollback()
                        raise
            else:
                # Only Telegram changes (no DB transaction needed)
                if is_muted is not None:
                    try:
                        agent.execute(_set_muted_status(bool(is_muted)), timeout=30.0)
                        logger.info(f"Set muted status to {is_muted} for channel {channel_id}")
                    except Exception as e:
                        logger.warning(f"Error setting muted status: {e}")
                        return jsonify({"error": f"Failed to set muted status: {str(e)}"}), 500
                if is_blocked is not None:
                    try:
                        agent.execute(_set_blocked_status(bool(is_blocked)), timeout=30.0)
                        logger.info(f"Set blocked status to {is_blocked} for channel {channel_id}")
                    except ValueError as e:
                        return jsonify({"error": str(e)}), 400
                    except Exception as e:
                        logger.warning(f"Error setting blocked status: {e}")
                        return jsonify({"error": f"Failed to set blocked status: {str(e)}"}), 500

            return jsonify({"success": True})
        except Exception as e:
            logger.error(f"Error updating conversation parameters for {agent_config_name}/{user_id}: {e}")
            return jsonify({"error": str(e)}), 500
