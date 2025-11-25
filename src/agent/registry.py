# agent/registry.py

# Copyright (c) 2025 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.

"""
Agent registry for managing all agents.
"""

import logging

logger = logging.getLogger(__name__)


class AgentRegistry:
    def __init__(self):
        self._registry = {}  # name -> Agent

    def all_agent_names(self):
        return list(self._registry.keys())

    def register(
        self,
        *,
        name: str,
        phone: str,
        instructions: str,
        role_prompt_names: list[str],
        llm=None,
        llm_name=None,
        sticker_set_names=None,
        explicit_stickers=None,
        config_directory=None,
        timezone=None,
    ):
        if name == "":
            raise RuntimeError("No agent name provided")
        if phone == "":
            raise RuntimeError("No agent phone provided")

        # Check for reserved names that conflict with state directory structure
        reserved_names = {"media"}
        if name.lower() in reserved_names:
            raise RuntimeError(
                f"Agent name '{name}' is reserved for system use. Please choose a different name."
            )

        # Import here to avoid circular dependency
        # Import from parent module (agent.py, not agent/ package)
        import importlib
        agent_module = importlib.import_module('agent')
        Agent = agent_module.Agent

        self._registry[name] = Agent(
            name=name,
            phone=phone,
            instructions=instructions,
            role_prompt_names=role_prompt_names,
            llm=llm,
            llm_name=llm_name,
            sticker_set_names=sticker_set_names,
            explicit_stickers=explicit_stickers,
            config_directory=config_directory,
            timezone=timezone,
        )
        # logger.info(f"Added agent [{name}] with instructions: {instructions!r}")

    def get_by_agent_id(self, agent_id):
        for agent in self.all_agents():
            if agent.agent_id == agent_id:
                return agent
        return None

    def all_agents(self):
        return self._registry.values()


_agent_registry = AgentRegistry()

register_telegram_agent = _agent_registry.register
get_agent_for_id = _agent_registry.get_by_agent_id
all_agents = _agent_registry.all_agents

