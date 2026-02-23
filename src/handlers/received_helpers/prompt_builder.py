# src/handlers/received_helpers/prompt_builder.py
#
# Copyright (c) 2025-2026 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.
#
import logging

from handlers.received_helpers.channel_details import build_channel_details_section
from utils import get_dialog_name
from utils.formatting import format_log_prefix, format_log_prefix_resolved
from schedule import get_current_activity
from telegram_media import get_unique_id

logger = logging.getLogger(__name__)


def _build_current_activity_section(agent, now, channel_name: str | None = None) -> str:
    """
    Build the current activity section for the system prompt.
    
    Args:
        agent: The agent instance
        now: Current datetime
        channel_name: Optional channel name for logging
    
    Returns:
        Formatted activity section string, or empty string if no activity
    """
    if not agent.daily_schedule_description:
        return ""
    
    try:
        schedule = agent._load_schedule()
        if not schedule:
            return ""
        
        current_activity, time_remaining, next_activity = get_current_activity(schedule, now)
        
        # If no current activity, show next activity if available
        if not current_activity:
            if not next_activity:
                return ""
            # Show next activity as upcoming
            activity_text = f"\n\n# Current Activity\n\n"
            activity_text += f"Next activity: {next_activity.activity_name} "
            activity_text += f"(starts at {next_activity.start_time.strftime('%I:%M %p')})\n"
            activity_text += f"{next_activity.description}\n"
            activity_text += "\nYou can retrieve your full schedule by accessing: file:schedule.json\n"
            return activity_text
        
        activity_text = f"\n\n# Current Activity\n\n"
        activity_text += f"You are currently: {current_activity.activity_name} "
        activity_text += f"({current_activity.start_time.strftime('%I:%M %p')} - {current_activity.end_time.strftime('%I:%M %p')})\n"
        activity_text += f"{current_activity.description}\n"
        
        # Add time remaining
        if time_remaining:
            hours = int(time_remaining.total_seconds() // 3600)
            minutes = int((time_remaining.total_seconds() % 3600) // 60)
            if hours > 0:
                time_str = f"{hours} hour{'s' if hours != 1 else ''}"
                if minutes > 0:
                    time_str += f" and {minutes} minute{'s' if minutes != 1 else ''}"
            else:
                time_str = f"{minutes} minute{'s' if minutes != 1 else ''}"
            activity_text += f"Time remaining: {time_str}\n"
        
        # Add next activity
        if next_activity:
            activity_text += f"Next activity: {next_activity.activity_name} (starts at {next_activity.start_time.strftime('%I:%M %p')})\n"
        
        activity_text += "\nYou can retrieve your full schedule by accessing: file:schedule.json\n"
        return activity_text
    except Exception as e:
        logger.debug(f"{format_log_prefix_resolved(agent.name, channel_name)} Failed to add current activity to prompt: {e}")
        return ""


def is_conversation_start(agent, messages, highest_summarized_id: int | None) -> bool:
    """
    Determine if this is the start of a conversation.
    
    It's a start ONLY if:
    1. Visible history is short (< 5 messages)
    2. None of the visible messages are from the agent
    3. None of the visible messages are already summarized (id <= highest_summarized_id)
    
    Args:
        agent: The agent instance
        messages: List of Telegram messages (full visible history)
        highest_summarized_id: Highest message ID summarized, or None
        
    Returns:
        True if this is the start of a conversation
    """
    # If any message is already summarized, it's not a start
    if highest_summarized_id is not None:
        for m in messages:
            msg_id = getattr(m, "id", None)
            if msg_id is not None and int(msg_id) <= highest_summarized_id:
                return False

    # Check if this looks like a new conversation based on visible history
    if len(messages) >= 5:
        return False

    agent_id = agent.agent_id
    # Check if any message is from the agent
    if agent_id is not None:
        for m in messages:
            # Check if message is from the agent
            if getattr(m, "out", False):
                # Message was sent by us
                return False
            # Also check from_id for compatibility
            if (
                getattr(m, "from_id", None)
                and getattr(m.from_id, "user_id", None) == agent_id
            ):
                return False
    
    # It's a start if history is short and there are no agent messages or summarized messages
    return True


async def build_specific_instructions(
    agent,
    channel_id: int,
    messages,
    target_msg,
    xsend_intent: str | None = None,
    reaction_messages=None,
    highest_summarized_id: int | None = None,
) -> str:
    """
    Compute the specific instructions for the system prompt based on context.

    Args:
        agent: The agent instance
        channel_id: The conversation ID
        messages: List of Telegram messages (full visible history)
        target_msg: Optional target message to respond to
        xsend_intent: Optional intent from a cross-channel send
        reaction_messages: Optional list of messages that received reactions
        highest_summarized_id: Highest message ID that has been summarized, or None

    Returns:
        Complete specific instructions string for the system prompt
    """
    channel_name = await get_dialog_name(agent, channel_id)
    
    # Check if this is conversation start
    is_start = is_conversation_start(agent, messages, highest_summarized_id)

    instructions = (
        "\n# Instruction\n\n"
        "You are acting as a user participating in chats on Telegram.\n"
        "Your response should take into account the following:\n\n"
    )
    instructions_count = 0
    any_instruction = False

    if xsend_intent:
        instructions += (
           "## Cross-channel Trigger (`xsend`)\n\n"
           "Begin your response with a `think` task, and react to the following intent,\n"
           "which was sent by you from another channel as an instruction *to yourself*.\n\n"
           "```\n"
           f"{xsend_intent}\n"
           "```\n"
        )
        any_instruction = True

    if is_start and not any_instruction:
        instructions += (
            "## New Conversation\n\n"
            "This is the start of a new conversation.\n"
            "Follow the instructions in the section `## Start Of Conversation`.\n"
        )
        any_instruction = True

    # Add target message instruction if provided (new messages take priority over reactions)
    if target_msg is not None and getattr(target_msg, "id", ""):
        instructions += (
            "## Target Message\n\n"
            "You are looking at this conversation because the messsage "
            f"with message_id {target_msg.id} was newly received.\n"
            "React to it if appropriate.\n"
        )
        any_instruction = True
    # Add reaction message instruction if this is a reaction-triggered task (and no new message)
    elif reaction_messages:
        if len(reaction_messages) == 1:
            # Single reaction - keep existing format
            instructions += (
                "## Reaction Received\n\n"
                f"Someone reacted to your message with message_id {reaction_messages[0].id}.\n"
                "Consider responding to acknowledge the reaction or continue the conversation.\n"
            )
        else:
            # Multiple reactions - show all message IDs
            msg_ids = ", ".join(str(m.id) for m in reaction_messages)
            instructions += (
                "## Multiple Reactions Received\n\n"
                f"People reacted to {len(reaction_messages)} of your messages (message_ids: {msg_ids}).\n"
                "Consider responding to acknowledge the reactions or continue the conversation.\n"
            )
        any_instruction = True

    if not any_instruction:
        instructions += (
            "## Conversation Continuation\n\n"
            "You are looking at this conversation and might need to continue it.\n"
            "React to it if appropriate.\n"
        )

    return instructions


async def build_complete_system_prompt(
    agent,
    channel_id: int,
    messages,
    media_chain,
    is_group: bool,
    channel_name: str,
    dialog,
    target_msg,
    xsend_intent: str | None = None,
    reaction_messages=None,
    graph=None,
    highest_summarized_id: int | None = None,
) -> str:
    """
    Build the complete system prompt with all sections.

    Args:
        agent: The agent instance
        channel_id: The conversation ID
        messages: List of Telegram messages
        media_chain: Media source chain for sticker descriptions
        is_group: Whether this is a group chat
        channel_name: Display name of the conversation partner
        dialog: Dialog entity
        target_msg: Optional target message to respond to
        xsend_intent: Optional intent from a cross-channel send
        reaction_msg: Optional reaction message
        graph: Optional TaskGraph to check for context resources
        highest_summarized_id: Highest message ID that has been summarized, or None

    Returns:
        Complete system prompt string
    """
    # Get base system prompt with context-appropriate instructions
    specific_instructions = await build_specific_instructions(
        agent=agent,
        channel_id=channel_id,
        messages=messages,
        target_msg=target_msg,
        xsend_intent=xsend_intent,
        reaction_messages=reaction_messages,
        highest_summarized_id=highest_summarized_id,
    )
    system_prompt = agent.get_system_prompt(channel_name, specific_instructions, channel_id=channel_id)
    log_prefix = await format_log_prefix(agent.name, channel_name)

    # Check if schedule.json is in context (as valid content, not an error)
    # If so, add Task-Schedule.md to the prompt after role prompts
    if graph is not None:
        fetched_resources = graph.context.get("fetched_resources", {})
        schedule_url = "file:schedule.json"
        
        if schedule_url in fetched_resources:
            schedule_content = fetched_resources[schedule_url]
            # Validate that it's valid JSON (not an error message)
            try:
                import json
                schedule_data = json.loads(schedule_content)
                if isinstance(schedule_data, dict):
                    # Valid schedule content - add Task-Schedule.md
                    from prompt_loader import load_system_prompt
                    task_schedule_prompt = load_system_prompt("Task-Schedule")
                    system_prompt += f"\n\n{task_schedule_prompt}"
                    logger.info(
                        f"{log_prefix} Added Task-Schedule.md to prompt (schedule.json found in context)"
                    )
            except (json.JSONDecodeError, ValueError, TypeError):
                # Not valid JSON - likely an error message, don't add Task-Schedule
                logger.debug(
                    f"{log_prefix} schedule.json in context but not valid JSON, skipping Task-Schedule.md"
                )

    # Build sticker list
    sticker_list = await _build_sticker_list(agent, media_chain)
    if sticker_list:
        system_prompt += f"\n\n# Stickers you may send using a `sticker` task\n\n{sticker_list}\n\n"
        system_prompt += "You may also send any sticker you've seen in chat or know about in any other way using the sticker set name and sticker name.\n"
        system_prompt += "Send stickers using the `sticker` task only, never using the `send` task."

    # Build media list (photos, audio, video, stickers without set, etc.)
    media_list = await _build_media_list(agent, media_chain)
    if media_list:
        system_prompt += f"\n\n# Media you may send using a `send_media` task\n\n{media_list}\n\n"
        system_prompt += "Send these items using the `send_media` task only, never using the `send` task."

    # Add memory content
    memory_content = agent._load_memory_content(channel_id)
    if memory_content:
        system_prompt += f"\n\n{memory_content}\n"
        logger.info(
            f"{log_prefix} Added memory content to system prompt for channel {channel_id}"
        )
    else:
        logger.info(f"{log_prefix} No memory content found for channel {channel_id}")

    # Add current time
    now = agent.get_current_time()
    system_prompt += (
        f"\n\n# Current Time\n\nThe current time is: {now.strftime('%A %B %d, %Y at %I:%M %p %Z')}"
    )

    # Add current activity if agent has a schedule
    activity_section = _build_current_activity_section(agent, now, channel_name)
    if activity_section:
        system_prompt += activity_section

    channel_details = await build_channel_details_section(
        agent=agent,
        channel_id=channel_id,
        dialog=dialog,
        media_chain=media_chain,
        channel_name=channel_name,
    )
    if channel_details:
        system_prompt += f"\n\n{channel_details}"

    # Add conversation summary immediately before the conversation history
    # Check if Task-Summarize role is present - if so, include full metadata
    has_task_summarize = "Task-Summarize" in getattr(agent, "role_prompt_names", [])
    summary_content = await agent._load_summary_content(channel_id, json_format=False, include_metadata=has_task_summarize)
    if summary_content:
        system_prompt += f"\n\n# Summary of earlier conversation\n\n{summary_content}\n"
        logger.info(
            f"{log_prefix} Added conversation summary to system prompt for channel {channel_id} "
            f"(with metadata: {has_task_summarize})"
        )

    # Repeat specific instructions at the end, after the conversation summary
    if specific_instructions:
        system_prompt += f"\n\n{specific_instructions}\n"

    return system_prompt


async def _is_sticker_sendable(agent, doc) -> bool:
    """
    Test if a sticker can be sent by checking for premium requirements.

    According to Telegram API documentation, premium stickers are identified by
    the presence of a videoSize of type=f in the sticker's main document.

    Args:
        agent: Agent instance
        doc: Sticker document from Telegram API

    Returns:
        True if sticker can be sent, False if it requires premium
    """
    try:
        # Check for premium indicator: videoSize with type=f
        video_thumbs = getattr(doc, "video_thumbs", None)
        if video_thumbs:
            for video_size in video_thumbs:
                video_type = getattr(video_size, "type", None)
                if video_type == "f":
                    return False

        # No premium indicators found
        return True

    except Exception as e:
        logger.exception(f"Error checking sticker sendability: {e}")
        return True


async def _build_sticker_list(agent, media_chain) -> str | None:
    """
    Build a formatted list of available stickers with descriptions.
    Filters out premium stickers that the agent cannot send.

    Args:
        agent: Agent instance with configured stickers
        media_chain: Media source chain for description lookups

    Returns:
        Formatted sticker list string or None if no stickers available
    """
    if not agent.stickers:
        return None

    lines: list[str] = []
    filtered_count = 0

    # Check if premium filtering is enabled (based on agent's premium status)
    filter_premium = getattr(agent, "filter_premium_stickers", True)

    if filter_premium:
        logger.debug("Premium sticker filtering enabled for non-premium agent")
    else:
        logger.debug("Premium sticker filtering disabled for premium agent")

    try:
        for set_short, name in sorted(agent.stickers.keys()):
            try:
                # Get the document from the configured stickers
                doc = agent.stickers.get((set_short, name))
                if doc:
                    # Check if sticker is sendable (not premium) if filtering is enabled
                    if filter_premium and not await _is_sticker_sendable(
                        agent, doc
                    ):
                        filtered_count += 1
                        continue

                    # Get unique_id from document
                    _uid = get_unique_id(doc)

                    # Use agent's media source chain
                    cache_record = await media_chain.get(
                        unique_id=_uid,
                        agent=agent,
                        doc=doc,
                        kind="sticker",
                        sticker_set_name=set_short,
                        sticker_name=name,
                        update_last_used=True,
                    )
                    desc = cache_record.get("description") if cache_record else None
                else:
                    desc = None
            except Exception as e:
                logger.exception(f"Failed to process sticker {set_short}::{name}: {e}")
                desc = None
            if desc:
                lines.append(f"- {set_short} :: {name} - {desc}")
            else:
                lines.append(f"- {set_short} :: {name}")

        if filtered_count > 0:
            logger.debug(f"Filtered out {filtered_count} premium stickers")

    except Exception as e:
        # If anything unexpected occurs, fall back to names-only list
        logger.warning(
            f"Failed to build sticker descriptions, falling back to names-only: {e}"
        )
        lines = [f"- {s} :: {n}" for (s, n) in sorted(agent.stickers.keys())]

    return "\n".join(lines) if lines else None


async def _build_media_list(agent, media_chain) -> str | None:
    """
    Build a formatted list of available media (photos, audio, video, stickers, etc.)
    with descriptions and kind. Uses agent.media with fallback to agent.photos.

    Args:
        agent: Agent instance with cached media
        media_chain: Media source chain for description/kind lookups

    Returns:
        Formatted media list string or None if no media available
    """
    media_cache = getattr(agent, "media", None) or getattr(agent, "photos", {})
    if not media_cache:
        return None

    lines: list[str] = []

    try:
        for unique_id_str in sorted(media_cache.keys()):
            try:
                media_obj = media_cache[unique_id_str]
                from telegram_media import get_unique_id
                _uid = get_unique_id(media_obj)

                cache_record = await media_chain.get(
                    unique_id=_uid,
                    agent=agent,
                    doc=media_obj,
                    kind=None,
                    update_last_used=True,
                )
                desc = cache_record.get("description") if cache_record else None
                kind = cache_record.get("kind", "document") if cache_record else "document"
            except Exception as e:
                logger.exception(f"Failed to process media {unique_id_str}: {e}")
                desc = None
                kind = "document"

            if desc:
                lines.append(f"- {unique_id_str} ({kind}) - {desc}")
            else:
                lines.append(f"- {unique_id_str} ({kind})")

    except Exception as e:
        logger.warning(
            f"Failed to build media descriptions, falling back to unique_ids-only: {e}"
        )
        lines = [f"- {uid}" for uid in sorted(media_cache.keys())]

    return "\n".join(lines) if lines else None
