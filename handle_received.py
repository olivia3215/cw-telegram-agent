# handle_received.py

import logging
import uuid
from task_graph import TaskNode
from agent import get_agent_for_id
from prompt_loader import load_raw_system_prompt_preamble

logger = logging.getLogger(__name__)

async def handle_received(task: TaskNode, graph):
    peer_id = graph.context.get("peer_id")
    agent_id = graph.context.get("agent_id")
    agent = get_agent_for_id(agent_id)
    client = agent.client
    llm = agent.llm

    if not peer_id or not agent_id or not client:
        raise RuntimeError("Missing context or Telegram client")

    # Compose prompts
    raw_preamble = load_raw_system_prompt_preamble()
    system_prompt = raw_preamble.replace("{{AGENT_NAME}}", agent.name) + "\n\n" + agent.instructions
    context_lines = task.params.get("thread_context", [])
    formatted_context = "\n".join(context_lines)
    user_message = task.params.get("message_text", "")

    user_prompt = (
        "Here is the conversation so far:\n\n"
        f"{formatted_context}\n\n"
        f"Compose a polite reply to the final message: {user_message}"
    )

    # Await LLM response
    reply = await llm.query(system_prompt, user_prompt)
    if reply == "":
        logger.info(f"LLM decided not to reply to {user_message}")
    else:
        # Add a send task with the generated message
        send_task = TaskNode(
            identifier=f"send-{uuid.uuid4().hex[:8]}",
            type="send",
            params={
                "to": peer_id,
                "message": reply,
                "in_reply_to": task.params.get("message_id"),
            },
            depends_on=[task.identifier]
        )

        graph.add_task(send_task)

    await client.send_read_acknowledge(peer_id)