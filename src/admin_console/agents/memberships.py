# admin_console/agents/memberships.py
#
# Group membership management routes for the admin console.

import asyncio
import logging
import re
from datetime import UTC, datetime, timedelta

from flask import Blueprint, jsonify, request  # pyright: ignore[reportMissingImports]
from telethon.tl.functions.account import UpdateNotifySettingsRequest  # pyright: ignore[reportMissingImports]
from telethon.tl.functions.channels import JoinChannelRequest, LeaveChannelRequest  # pyright: ignore[reportMissingImports]
from telethon.tl.functions.messages import ImportChatInviteRequest  # pyright: ignore[reportMissingImports]
from telethon.tl.types import (  # pyright: ignore[reportMissingImports]
    Chat,
    Channel,
    InputPeerNotifySettings,
    User,
)

from admin_console.helpers import get_agent_by_name
from utils import normalize_peer_id
from utils.telegram import is_group_or_channel

logger = logging.getLogger(__name__)


def register_membership_routes(agents_bp: Blueprint):
    """Register group membership management routes."""

    @agents_bp.route("/api/agents/<agent_config_name>/memberships", methods=["GET"])
    def api_get_memberships(agent_config_name: str):
        """Get list of groups/channels the agent is a member of."""
        try:
            agent = get_agent_by_name(agent_config_name)
            if not agent:
                return jsonify({"error": f"Agent '{agent_config_name}' not found"}), 404

            if not agent.is_authenticated:
                return jsonify({"error": "Agent not authenticated"}), 503

            client = agent.client
            if not client or not client.is_connected():
                return jsonify({"error": "Agent client not connected"}), 503

            # Fetch groups/channels from Telegram
            async def _fetch_memberships():
                """Fetch group memberships - runs in agent's event loop."""
                memberships = []
                try:
                    # Check if agent's event loop is accessible
                    client_loop = agent._get_client_loop()
                    if not client_loop or not client_loop.is_running():
                        raise RuntimeError("Agent client event loop is not accessible or not running")
                except Exception as e:
                    logger.warning(f"Cannot fetch memberships - event loop check failed: {e}")
                    return []

                try:
                    # Iterate through dialogs to find groups/channels
                    async for dialog in client.iter_dialogs():
                        # Sleep to avoid rate limiting
                        await asyncio.sleep(0.05)

                        entity = dialog.entity

                        # Only include groups and channels (not DMs)
                        if not is_group_or_channel(entity):
                            continue

                        # Get dialog ID and normalize
                        dialog_id = dialog.id
                        try:
                            if hasattr(dialog_id, 'user_id'):
                                dialog_id = dialog_id.user_id
                            elif isinstance(dialog_id, int):
                                pass
                            else:
                                dialog_id = int(dialog_id)
                            channel_id = str(normalize_peer_id(dialog_id))
                        except Exception as e:
                            logger.warning(f"Error normalizing peer ID for dialog {dialog.id}: {e}")
                            continue

                        # Get name from entity
                        name = None
                        if hasattr(entity, "title") and entity.title:
                            name = entity.title.strip()

                        # Get username if available
                        username = None
                        if hasattr(entity, "username") and entity.username:
                            username = entity.username
                        elif hasattr(entity, "usernames") and entity.usernames:
                            for handle in entity.usernames:
                                handle_value = getattr(handle, "username", None)
                                if handle_value:
                                    username = handle_value
                                    break

                        # Check mute status
                        is_muted = await agent.is_muted(dialog_id)

                        memberships.append({
                            "channel_id": channel_id,
                            "name": name,
                            "username": username,
                            "is_muted": is_muted,
                        })

                except Exception as e:
                    logger.warning(f"Error fetching memberships: {e}", exc_info=True)

                return memberships

            # Use agent.execute() to run the coroutine on the agent's event loop
            try:
                memberships = agent.execute(_fetch_memberships(), timeout=30.0)
            except RuntimeError as e:
                error_msg = str(e).lower()
                if "event loop" in error_msg or "not authenticated" in error_msg or "not running" in error_msg:
                    logger.warning(f"Cannot fetch memberships: {e}")
                    return jsonify({"error": "Cannot fetch memberships - agent client not available"}), 503
                else:
                    logger.warning(f"RuntimeError fetching memberships: {e}", exc_info=True)
                    return jsonify({"error": str(e)}), 500
            except TimeoutError as e:
                logger.warning(f"Timeout fetching memberships for agent {agent_config_name}: {e}")
                return jsonify({"error": "Timeout fetching memberships"}), 500
            except Exception as e:
                logger.error(f"Error fetching memberships for {agent_config_name}: {e}", exc_info=True)
                return jsonify({"error": str(e)}), 500

            # Sort by name
            memberships.sort(key=lambda x: (x["name"] or "").lower())

            return jsonify({"memberships": memberships})
        except Exception as e:
            logger.error(f"Error getting memberships for {agent_config_name}: {e}")
            return jsonify({"error": str(e)}), 500

    @agents_bp.route("/api/agents/<agent_config_name>/memberships/subscribe", methods=["POST"])
    def api_subscribe(agent_config_name: str):
        """Subscribe to a group/channel by identifier or invitation link."""
        try:
            agent = get_agent_by_name(agent_config_name)
            if not agent:
                return jsonify({"error": f"Agent '{agent_config_name}' not found"}), 404

            if not agent.is_authenticated:
                return jsonify({"error": "Agent not authenticated"}), 503

            data = request.json
            identifier = data.get("identifier", "").strip()

            if not identifier:
                return jsonify({"error": "Identifier is required"}), 400

            client = agent.client
            if not client or not client.is_connected():
                return jsonify({"error": "Agent client not connected"}), 503

            async def _subscribe():
                """Subscribe to group/channel - runs in agent's event loop."""
                try:
                    entity = None
                    channel_id = None
                    joined_via_invite = False

                    # Check if it's an invitation link
                    invite_match = re.search(r'(?:t\.me/joinchat/|t\.me/\+)([A-Za-z0-9_-]+)', identifier)
                    if invite_match:
                        # It's an invitation link
                        invite_hash = invite_match.group(1)
                        try:
                            # Get list of existing channel IDs before joining
                            # Note: We iterate through all dialogs (no limit) to ensure we capture
                            # all existing channels, avoiding false positives when searching for the new channel
                            existing_channel_ids = set()
                            async for dialog in client.iter_dialogs():
                                dialog_entity = dialog.entity
                                if is_group_or_channel(dialog_entity):
                                    try:
                                        dialog_id = dialog.id
                                        if hasattr(dialog_id, 'user_id'):
                                            dialog_id = dialog_id.user_id
                                        elif not isinstance(dialog_id, int):
                                            dialog_id = int(dialog_id)
                                        existing_channel_ids.add(normalize_peer_id(dialog_id))
                                    except Exception:
                                        pass
                            
                            result = await client(ImportChatInviteRequest(invite_hash))
                            joined_via_invite = True
                            
                            # ImportChatInviteRequest returns updates, extract the chat from there
                            if hasattr(result, 'chats') and result.chats:
                                entity = result.chats[0]
                                channel_id = normalize_peer_id(entity.id)
                            else:
                                # If result.chats is empty, find the newly joined chat from dialogs
                                # by comparing before/after channel IDs
                                # Note: We iterate through all dialogs (no limit) to ensure we find
                                # the newly joined channel even if the agent has many dialogs
                                entity = None
                                channel_id = None
                                
                                # Iterate through dialogs to find the newly joined group/channel
                                async for dialog in client.iter_dialogs():
                                    dialog_entity = dialog.entity
                                    if is_group_or_channel(dialog_entity):
                                        try:
                                            dialog_id = dialog.id
                                            if hasattr(dialog_id, 'user_id'):
                                                dialog_id = dialog_id.user_id
                                            elif not isinstance(dialog_id, int):
                                                dialog_id = int(dialog_id)
                                            normalized_id = normalize_peer_id(dialog_id)
                                            
                                            # Check if this is a new channel (not in existing list)
                                            if normalized_id not in existing_channel_ids:
                                                entity = dialog_entity
                                                channel_id = normalized_id
                                                break
                                        except Exception as e:
                                            logger.warning(f"Error normalizing peer ID for dialog {dialog.id}: {e}")
                                            continue
                                
                                if not entity or not channel_id:
                                    # Could not find the chat - still try to proceed but log a warning
                                    logger.warning(f"Successfully joined via invite link but could not identify the chat for muting")
                                    # Return error so user knows muting failed, even though join succeeded
                                    return {"error": "Successfully joined group but could not determine chat ID for muting"}
                        except Exception as e:
                            logger.warning(f"Error importing chat invite: {e}")
                            return {"error": f"Failed to join via invitation link: {str(e)}"}
                    else:
                        # Try to resolve as username, ID, or phone number
                        try:
                            entity = await client.get_entity(identifier)
                            if not is_group_or_channel(entity):
                                return {"error": "Identifier does not refer to a group or channel"}
                            channel_id = normalize_peer_id(entity.id)
                        except Exception as e:
                            logger.warning(f"Error getting entity for {identifier}: {e}")
                            return {"error": f"Could not find group/channel: {str(e)}"}

                    # If we have an entity and channel_id, proceed with join (if needed) and mute
                    if entity and channel_id:
                        # For invite links, we've already joined, so skip the join step
                        if not joined_via_invite:
                            # Actually join the channel/group
                            # For channels and supergroups, use JoinChannelRequest
                            # For basic groups, if we can resolve the entity, we're likely already a member
                            try:
                                if isinstance(entity, Channel):
                                    # Join channel or supergroup
                                    await client(JoinChannelRequest(entity))
                                elif isinstance(entity, Chat):
                                    # For basic groups, if we can resolve the entity via get_entity(),
                                    # we're likely already a member. If not, we'd need an invite link.
                                    # Since we already resolved it successfully, assume we're a member.
                                    pass
                                else:
                                    return {"error": "Unknown entity type"}
                            except Exception as e:
                                error_str = str(e).lower()
                                # If we're already a member, that's fine - continue
                                if "already" in error_str or "participant" in error_str:
                                    pass  # Already a member, continue
                                else:
                                    logger.warning(f"Error joining channel/group: {e}")
                                    return {"error": f"Failed to join: {str(e)}"}

                        # Mute immediately as requested
                        mute_warning = None
                        try:
                            await _set_mute_status(client, channel_id, True)
                        except Exception as mute_error:
                            # Join succeeded but muting failed - log warning but don't fail the subscription
                            logger.warning(f"Successfully joined group/channel but failed to mute: {mute_error}")
                            mute_warning = f"Successfully joined but could not mute: {str(mute_error)}"

                        response = {
                            "success": True,
                            "channel_id": str(channel_id),
                            "name": getattr(entity, "title", None) or identifier,
                        }
                        if mute_warning:
                            response["warning"] = mute_warning
                        return response
                    else:
                        return {"error": "Could not determine channel ID"}

                except Exception as e:
                    logger.warning(f"Error subscribing to group/channel: {e}", exc_info=True)
                    return {"error": str(e)}

            # Use agent.execute() to run the coroutine on the agent's event loop
            try:
                result = agent.execute(_subscribe(), timeout=30.0)
                if result.get("error"):
                    return jsonify({"error": result["error"]}), 400
                return jsonify(result)
            except RuntimeError as e:
                error_msg = str(e).lower()
                if "event loop" in error_msg or "not authenticated" in error_msg:
                    return jsonify({"error": "Cannot subscribe - agent client not available"}), 503
                else:
                    logger.warning(f"RuntimeError subscribing: {e}", exc_info=True)
                    return jsonify({"error": str(e)}), 500
            except TimeoutError as e:
                logger.warning(f"Timeout subscribing for agent {agent_config_name}: {e}")
                return jsonify({"error": "Timeout subscribing to group/channel"}), 500
            except Exception as e:
                logger.error(f"Error subscribing for {agent_config_name}: {e}", exc_info=True)
                return jsonify({"error": str(e)}), 500

        except Exception as e:
            logger.error(f"Error subscribing for {agent_config_name}: {e}")
            return jsonify({"error": str(e)}), 500

    @agents_bp.route("/api/agents/<agent_config_name>/memberships/<channel_id>", methods=["DELETE"])
    def api_delete_membership(agent_config_name: str, channel_id: str):
        """Delete/leave a group/channel subscription."""
        try:
            agent = get_agent_by_name(agent_config_name)
            if not agent:
                return jsonify({"error": f"Agent '{agent_config_name}' not found"}), 404

            if not agent.is_authenticated:
                return jsonify({"error": "Agent not authenticated"}), 503

            client = agent.client
            if not client or not client.is_connected():
                return jsonify({"error": "Agent client not connected"}), 503

            async def _leave():
                """Leave group/channel - runs in agent's event loop."""
                try:
                    # Normalize channel_id
                    try:
                        channel_id_int = int(channel_id)
                        channel_id_normalized = normalize_peer_id(channel_id_int)
                    except (ValueError, TypeError):
                        return {"error": f"Invalid channel ID: {channel_id}"}

                    # Get entity to determine if it's a channel or group
                    entity = await agent.get_cached_entity(channel_id_normalized)
                    if not entity:
                        # Try to get it directly
                        try:
                            entity = await client.get_entity(channel_id_normalized)
                        except Exception as e:
                            logger.warning(f"Could not get entity for {channel_id}: {e}")
                            return {"error": f"Could not find group/channel: {str(e)}"}

                    if not is_group_or_channel(entity):
                        return {"error": "Channel ID does not refer to a group or channel"}

                    # Leave the channel/group
                    if isinstance(entity, Channel):
                        # Use LeaveChannelRequest for channels
                        await client(LeaveChannelRequest(entity))
                    elif isinstance(entity, Chat):
                        # For groups, use DeleteChatUserRequest (leave group)
                        from telethon.tl.functions.messages import DeleteChatUserRequest  # pyright: ignore[reportMissingImports]

                        me = await client.get_me()
                        if not me:
                            return {"error": "Could not get agent's user info"}
                        await client(DeleteChatUserRequest(chat_id=entity.id, user_id=me))
                    else:
                        return {"error": "Unknown entity type"}

                    return {"success": True}

                except Exception as e:
                    logger.warning(f"Error leaving group/channel: {e}", exc_info=True)
                    return {"error": str(e)}

            # Use agent.execute() to run the coroutine on the agent's event loop
            try:
                result = agent.execute(_leave(), timeout=30.0)
                if result.get("error"):
                    return jsonify({"error": result["error"]}), 400
                return jsonify(result)
            except RuntimeError as e:
                error_msg = str(e).lower()
                if "event loop" in error_msg or "not authenticated" in error_msg:
                    return jsonify({"error": "Cannot leave - agent client not available"}), 503
                else:
                    logger.warning(f"RuntimeError leaving: {e}", exc_info=True)
                    return jsonify({"error": str(e)}), 500
            except TimeoutError as e:
                logger.warning(f"Timeout leaving for agent {agent_config_name}: {e}")
                return jsonify({"error": "Timeout leaving group/channel"}), 500
            except Exception as e:
                logger.error(f"Error leaving for {agent_config_name}: {e}", exc_info=True)
                return jsonify({"error": str(e)}), 500

        except Exception as e:
            logger.error(f"Error leaving for {agent_config_name}: {e}")
            return jsonify({"error": str(e)}), 500

    @agents_bp.route("/api/agents/<agent_config_name>/memberships/<channel_id>/mute", methods=["PUT"])
    def api_toggle_mute(agent_config_name: str, channel_id: str):
        """Toggle mute status for a group/channel."""
        try:
            agent = get_agent_by_name(agent_config_name)
            if not agent:
                return jsonify({"error": f"Agent '{agent_config_name}' not found"}), 404

            if not agent.is_authenticated:
                return jsonify({"error": "Agent not authenticated"}), 503

            data = request.json
            is_muted = data.get("is_muted", False)

            client = agent.client
            if not client or not client.is_connected():
                return jsonify({"error": "Agent client not connected"}), 503

            async def _toggle_mute():
                """Toggle mute status - runs in agent's event loop."""
                try:
                    # Normalize channel_id
                    try:
                        channel_id_int = int(channel_id)
                        channel_id_normalized = normalize_peer_id(channel_id_int)
                    except (ValueError, TypeError):
                        return {"error": f"Invalid channel ID: {channel_id}"}

                    # Get entity
                    entity = await agent.get_cached_entity(channel_id_normalized)
                    if not entity:
                        try:
                            entity = await client.get_entity(channel_id_normalized)
                        except Exception as e:
                            logger.warning(f"Could not get entity for {channel_id}: {e}")
                            return {"error": f"Could not find group/channel: {str(e)}"}

                    if not is_group_or_channel(entity):
                        return {"error": "Channel ID does not refer to a group or channel"}

                    # Set mute status
                    await _set_mute_status(client, channel_id_normalized, is_muted)

                    # Invalidate cache so next check gets fresh data
                    if agent.api_cache and hasattr(agent.api_cache, "_mute_cache"):
                        agent.api_cache._mute_cache.pop(channel_id_normalized, None)

                    return {"success": True, "is_muted": is_muted}

                except Exception as e:
                    logger.warning(f"Error toggling mute: {e}", exc_info=True)
                    return {"error": str(e)}

            # Use agent.execute() to run the coroutine on the agent's event loop
            try:
                result = agent.execute(_toggle_mute(), timeout=30.0)
                if result.get("error"):
                    return jsonify({"error": result["error"]}), 400
                return jsonify(result)
            except RuntimeError as e:
                error_msg = str(e).lower()
                if "event loop" in error_msg or "not authenticated" in error_msg:
                    return jsonify({"error": "Cannot toggle mute - agent client not available"}), 503
                else:
                    logger.warning(f"RuntimeError toggling mute: {e}", exc_info=True)
                    return jsonify({"error": str(e)}), 500
            except TimeoutError as e:
                logger.warning(f"Timeout toggling mute for agent {agent_config_name}: {e}")
                return jsonify({"error": "Timeout toggling mute"}), 500
            except Exception as e:
                logger.error(f"Error toggling mute for {agent_config_name}: {e}", exc_info=True)
                return jsonify({"error": str(e)}), 500

        except Exception as e:
            logger.error(f"Error toggling mute for {agent_config_name}: {e}")
            return jsonify({"error": str(e)}), 500


async def _set_mute_status(client, peer_id: int, mute: bool):
    """Set mute status for a peer."""
    if mute:
        # Mute forever (set mute_until to a far future date)
        mute_until = datetime.now(UTC) + timedelta(days=365 * 100)  # 100 years
        settings = InputPeerNotifySettings(
            show_previews=None,
            silent=True,
            mute_until=int(mute_until.timestamp()),
            sound=None,
        )
    else:
        # Unmute (set mute_until to None or past date)
        settings = InputPeerNotifySettings(
            show_previews=None,
            silent=False,
            mute_until=None,
            sound=None,
        )

    await client(UpdateNotifySettingsRequest(peer=peer_id, settings=settings))
