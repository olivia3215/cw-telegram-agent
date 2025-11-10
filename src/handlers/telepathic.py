from __future__ import annotations

import logging

from telepathic import is_telepath

logger = logging.getLogger(__name__)


async def maybe_send_telepathic_message(agent, channel_id: int, prefix: str, content: str):
    """
    Send a telepathic message to a channel immediately.

    Args:
        agent: The agent instance.
        channel_id: The channel to send to.
        prefix: The concept (e.g., "think", "remember", "retrieve").
        content: The message body (without prefix markers).
    """
    if not content or not content.strip():
        return

    if agent is None:
        logger.info("Skipping telepathic message: missing agent context")
        return

    should_reveal = is_telepath(channel_id) and not is_telepath(getattr(agent, "agent_id", None))
    if not should_reveal:
        if not is_telepath(channel_id):
            logger.info(
                f"[{getattr(agent, 'name', 'unknown-agent')}] "
                f"Skipping telepathic message: channel {channel_id} is not telepathic"
            )
        if is_telepath(getattr(agent, "agent_id", None)):
            logger.info(
                f"[{getattr(agent, 'name', 'unknown-agent')}] "
                f"Skipping telepathic message: agent {getattr(agent, 'agent_id', None)} is telepathic"
            )
        return

    prefix_stripped = prefix.strip()
    if prefix_stripped.startswith("⟦") and prefix_stripped.endswith("⟧"):
        prefix_stripped = prefix_stripped[1:-1]

    message = f"⟦{prefix_stripped}⟧\n{content}"
    try:
        await agent.client.send_message(channel_id, message, parse_mode="Markdown")
        logger.info(f"[{getattr(agent, 'name', 'unknown-agent')}] Sent telepathic message: {prefix}")
    except Exception as exc:  # pragma: no cover - log unexpected client exception
        logger.error(
            f"[{getattr(agent, 'name', 'unknown-agent')}] Failed to send telepathic message: {exc}"
        )

