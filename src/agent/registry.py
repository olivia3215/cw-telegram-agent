# src/agent/registry.py
#
# Copyright (c) 2025-2026 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.
#
"""
Agent registry for managing all agents.
"""

import logging

logger = logging.getLogger(__name__)


class AgentRegistry:
    def __init__(self):
        self._registry = {}  # config_name -> Agent

    def all_agent_names(self):
        """Return list of agent config names (for backward compatibility with tests)."""
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
        config_directory=None,
        config_name=None,
        timezone=None,
        daily_schedule_description=None,
        reset_context_on_first_message=False,
        clear_summaries_on_first_message=False,
        is_disabled=False,
        is_gagged=False,
        start_typing_delay=None,
        typing_speed=None,
    ):
        if name == "":
            raise RuntimeError("No agent name provided")
        if phone == "":
            raise RuntimeError("No agent phone provided")
        if config_name == "":
            raise RuntimeError("Agent config_name cannot be empty string (use None to default to name)")

        # Check for reserved names that conflict with state directory structure
        # State directories use config_name (or name if config_name is not provided)
        reserved_names = {"media"}
        
        # Determine the effective config_name that will be used for state directories
        effective_config_name = config_name or name
        
        if effective_config_name.lower() in reserved_names:
            # Use the actual value that conflicts for better error message
            conflicting_value = config_name or name
            field_name = "config name" if config_name else "name"
            raise RuntimeError(
                f"Agent {field_name} '{conflicting_value}' is reserved for system use. Please choose a different name."
            )

        # Import here to avoid circular dependency
        # Import from parent module (agent.py, not agent/ package)
        import importlib
        agent_module = importlib.import_module('agent')
        Agent = agent_module.Agent

        # Check for duplicate config_name in registry
        # (effective_config_name was already computed above for reserved name check)
        if effective_config_name in self._registry:
            existing_agent = self._registry[effective_config_name]
            raise RuntimeError(
                f"Agent with config_name '{effective_config_name}' already registered "
                f"(existing: name='{existing_agent.name}', new: name='{name}'). "
                f"Config names must be unique."
            )

        agent = Agent(
            name=name,
            phone=phone,
            instructions=instructions,
            role_prompt_names=role_prompt_names,
            llm=llm,
            llm_name=llm_name,
            sticker_set_names=sticker_set_names,
            config_directory=config_directory,
            config_name=config_name,
            timezone=timezone,
            daily_schedule_description=daily_schedule_description,
            reset_context_on_first_message=reset_context_on_first_message,
            clear_summaries_on_first_message=clear_summaries_on_first_message,
            is_disabled=is_disabled,
            is_gagged=is_gagged,
            start_typing_delay=start_typing_delay,
            typing_speed=typing_speed,
        )
        
        # Store by config_name (the key used for state directories and lookups)
        self._registry[effective_config_name] = agent
        # logger.info(f"Added agent [{name}] with config_name [{effective_config_name}] with instructions: {instructions!r}")

    def get_by_config_name(self, config_name: str):
        """Get an agent by config_name (the registry key)."""
        return self._registry.get(config_name)

    def get_by_agent_id(self, agent_id):
        """Get an agent by Telegram agent_id. Accepts int or string (e.g. from JSON)."""
        try:
            agent_id_int = int(agent_id)
        except (TypeError, ValueError):
            return None
        for agent in self.all_agents(include_disabled=True):
            if agent.agent_id == agent_id_int:
                return agent
        return None

    def all_agents(self, include_disabled: bool = False):
        if include_disabled:
            return self._registry.values()
        return [agent for agent in self._registry.values() if not agent.is_disabled]

    def clear(self):
        """Clear all registered agents."""
        self._registry.clear()


_agent_registry = AgentRegistry()

register_telegram_agent = _agent_registry.register
get_agent_for_id = _agent_registry.get_by_agent_id
all_agents = _agent_registry.all_agents
