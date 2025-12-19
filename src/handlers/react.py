import logging

from telethon.tl.functions.messages import SendReactionRequest
from telethon.tl.types import ReactionEmoji

from agent import get_agent_for_id
from handlers.registry import register_task_handler
from task_graph import TaskNode
from telegram_util import get_channel_name
from utils import coerce_to_int

logger = logging.getLogger(__name__)


@register_task_handler("react")
async def handle_react(task: TaskNode, graph, work_queue=None):
    """
    Deliver a react task by adding an emoji reaction to a specific message.
    """
    agent_id = graph.context.get("agent_id")
    channel_id = graph.context.get("channel_id")

    if not agent_id:
        raise ValueError("Missing 'agent_id' in task graph context")
    if channel_id is None:
        raise ValueError(f"Missing required 'channel_id' field in task {task.id}")

    agent = get_agent_for_id(agent_id)
    if not agent:
        raise RuntimeError(f"Agent with id {agent_id} not found")

    client = agent.client
    if not client:
        raise RuntimeError(f"No Telegram client registered for agent_id {agent_id}")

    emoji_raw = task.params.get("emoji")
    emoji = str(emoji_raw or "").strip()
    if not emoji:
        raise ValueError(f"Task {task.id} missing required 'emoji' parameter")

    message_id = coerce_to_int(task.params.get("message_id"))
    if not message_id:
        raise ValueError(f"Task {task.id} missing or invalid 'message_id' parameter")

    channel_name = await get_channel_name(agent, channel_id)

    # Convert channel_id to integer and resolve entity
    try:
        channel_id_int = int(channel_id)
    except (ValueError, TypeError):
        channel_id_int = channel_id

    # Get the entity first to ensure it's resolved (important for channels)
    entity = await agent.get_cached_entity(channel_id_int)
    if not entity:
        # Fallback to channel_id_int if entity resolution fails
        entity = channel_id_int

    logger.info(
        f"[{agent.name}] REACT: to=[{channel_name}] message_id={message_id} emoji={emoji}"
    )

    request = SendReactionRequest(
        peer=entity,
        msg_id=message_id,
        reaction=[ReactionEmoji(emoticon=emoji)],
    )

    try:
        await client(request)
    except Exception as exc:
        logger.exception(
            f"[{agent.name}] Failed to send reaction to message {message_id}: {exc}"
        )
