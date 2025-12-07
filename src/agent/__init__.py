# agent/__init__.py

# Copyright (c) 2025 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.

"""
Agent package - provides Agent class and registry functions.

This package re-exports the Agent class and registry functions from the
parent agent.py module for backward compatibility.

Uses lazy imports to avoid circular dependency issues.
"""

# Lazy import to avoid circular dependencies
# When Agent is accessed, import it from the parent module
def __getattr__(name):
    if name in ("Agent", "AgentRegistry", "all_agents", "get_agent_for_id", "register_telegram_agent", "_agent_registry"):
        # Import the parent agent.py module
        import importlib
        import sys
        
        # Get the parent directory and import agent.py directly
        import os
        parent_dir = os.path.dirname(os.path.dirname(__file__))
        if parent_dir not in sys.path:
            sys.path.insert(0, parent_dir)
        
        # Import agent module (the .py file, not this package)
        # We need to import it from the file system directly
        import importlib.util
        agent_file = os.path.join(parent_dir, "agent.py")
        spec = importlib.util.spec_from_file_location("agent_module", agent_file)
        agent_module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(agent_module)
        
        # Cache the imports
        globals()["Agent"] = agent_module.Agent
        globals()["AgentRegistry"] = agent_module.AgentRegistry
        globals()["all_agents"] = agent_module.all_agents
        globals()["get_agent_for_id"] = agent_module.get_agent_for_id
        globals()["register_telegram_agent"] = agent_module.register_telegram_agent
        globals()["_agent_registry"] = agent_module._agent_registry
        
        return globals()[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

__all__ = [
    "Agent",
    "AgentRegistry",
    "all_agents",
    "get_agent_for_id",
    "register_telegram_agent",
    "_agent_registry",
]
