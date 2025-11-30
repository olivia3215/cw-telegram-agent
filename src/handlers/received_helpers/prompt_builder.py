# handlers/received_helpers/prompt_builder.py
#
# System prompt building utilities for LLM queries.

import logging

from handlers.received_helpers.channel_details import _build_channel_details_section
from utils import get_dialog_name

logger = logging.getLogger(__name__)


async def build_specific_instructions(
    agent,
    channel_id: int,
    messages,
    target_msg,
    xsend_intent: str | None = None,
    reaction_msg=None,
) -> str:
    """
    Compute the specific instructions for the system prompt based on context.

    Args:
        agent: The agent instance
        channel_id: The conversation ID
        messages: List of Telegram messages
        target_msg: Optional target message to respond to
        xsend_intent: Optional intent from a cross-channel send

    Returns:
        Complete specific instructions string for the system prompt
    """
    channel_name = await get_dialog_name(agent, channel_id)
    
    # Check if this is conversation start
    is_conversation_start = len(messages) < 5
    agent_id = agent.agent_id
    if is_conversation_start and agent_id is not None:
        for m in messages:
            if (
                getattr(m, "from_id", None)
                and getattr(m.from_id, "user_id", None) == agent_id
            ):
                is_conversation_start = False
                break

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

    if is_conversation_start and not any_instruction:
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
    elif reaction_msg is not None and getattr(reaction_msg, "id", ""):
        instructions += (
            "## Reaction Received\n\n"
            f"Someone reacted to your message with message_id {reaction_msg.id}.\n"
            "Consider responding to acknowledge the reaction or continue the conversation.\n"
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
    reaction_msg=None,
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
        target_msg: Optional target message to respond to
        xsend_intent: Optional intent from a cross-channel send

    Returns:
        Complete system prompt string
    """
    from handlers.received import _build_sticker_list
    
    # Get base system prompt with context-appropriate instructions
    specific_instructions = await build_specific_instructions(
        agent=agent,
        channel_id=channel_id,
        messages=messages,
        target_msg=target_msg,
        xsend_intent=xsend_intent,
        reaction_msg=reaction_msg,
    )
    system_prompt = agent.get_system_prompt(channel_name, specific_instructions, channel_id=channel_id)

    # Build sticker list
    sticker_list = await _build_sticker_list(agent, media_chain)
    if sticker_list:
        system_prompt += f"\n\n# Stickers you may send\n\n{sticker_list}\n"
        system_prompt += "\n\nYou may also send any sticker you've seen in chat or know about in any other way using the sticker set name and sticker name.\n"

    # Add memory content
    memory_content = agent._load_memory_content(channel_id)
    if memory_content:
        system_prompt += f"\n\n{memory_content}\n"
        logger.info(
            f"[{agent.name}] Added memory content to system prompt for channel {channel_id}"
        )
    else:
        logger.info(f"[{agent.name}] No memory content found for channel {channel_id}")

    # Add current time
    now = agent.get_current_time()
    system_prompt += (
        f"\n\n# Current Time\n\nThe current time is: {now.strftime('%A %B %d, %Y at %I:%M %p %Z')}"
    )

    channel_details = await _build_channel_details_section(
        agent=agent,
        channel_id=channel_id,
        dialog=dialog,
        media_chain=media_chain,
        channel_name=channel_name,
    )
    if channel_details:
        system_prompt += f"\n\n{channel_details}\n"

    # Add conversation summary immediately before the conversation history
    summary_content = await agent._load_summary_content(channel_id, json_format=False)
    if summary_content:
        system_prompt += f"\n\n# Conversation Summary\n\n{summary_content}\n"
        logger.info(
            f"[{agent.name}] Added conversation summary to system prompt for channel {channel_id}"
        )

    # Repeat specific instructions at the end, after the conversation summary
    if specific_instructions:
        system_prompt += f"\n\n{specific_instructions}\n"

    return system_prompt

