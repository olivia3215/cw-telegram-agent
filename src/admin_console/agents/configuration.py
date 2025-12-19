# admin_console/agents/configuration.py
#
# Agent configuration management routes for the admin console.

import logging
from pathlib import Path

from flask import Blueprint, jsonify, request  # pyright: ignore[reportMissingImports]

from admin_console.helpers import get_agent_by_name, get_available_llms, get_available_timezones, get_default_llm

logger = logging.getLogger(__name__)


def register_configuration_routes(agents_bp: Blueprint):
    """Register agent configuration routes."""
    register_new_agent_routes(agents_bp)
    
    @agents_bp.route("/api/agents/<agent_config_name>/configuration", methods=["GET"])
    def api_get_agent_configuration(agent_config_name: str):
        """Get agent configuration (LLM and prompt)."""
        try:
            agent = get_agent_by_name(agent_config_name)
            if not agent:
                return jsonify({"error": f"Agent '{agent_config_name}' not found"}), 404

            # Get current LLM (agent's configured LLM or default)
            current_llm = agent._llm_name or get_default_llm()
            available_llms = get_available_llms()

            # Mark which LLM is the default
            default_llm = get_default_llm()
            for llm in available_llms:
                if llm["value"] == default_llm:
                    llm["is_default"] = True
                else:
                    llm["is_default"] = False

            # Get current timezone (IANA timezone string or None)
            # Only return a timezone if explicitly configured; otherwise return None
            # so the frontend shows "Server Default" selected
            current_timezone = agent._timezone_raw if agent._timezone_raw else None
            
            available_timezones = get_available_timezones()

            return jsonify({
                "name": agent.name,
                "llm": current_llm,
                "available_llms": available_llms,
                "prompt": agent.instructions,
                "timezone": current_timezone,
                "available_timezones": available_timezones,
                "is_disabled": agent.is_disabled,
            })
        except Exception as e:
            logger.error(f"Error getting configuration for {agent_config_name}: {e}")
            return jsonify({"error": str(e)}), 500

    @agents_bp.route("/api/agents/<agent_config_name>/configuration/llm", methods=["PUT"])
    def api_update_agent_llm(agent_config_name: str):
        """Update agent LLM."""
        try:
            agent = get_agent_by_name(agent_config_name)
            if not agent:
                return jsonify({"error": f"Agent '{agent_config_name}' not found"}), 404

            if not agent.config_directory:
                return jsonify({"error": "Agent has no config directory"}), 400

            data = request.json
            llm_name = data.get("llm_name", "").strip()

            # Find agent's markdown file
            agent_file = Path(agent.config_directory) / "agents" / f"{agent.config_name}.md"
            if not agent_file.exists():
                return jsonify({"error": "Agent configuration file not found"}), 404

            # Read and parse the markdown file
            content = agent_file.read_text(encoding="utf-8")
            from register_agents import extract_fields_from_markdown
            fields = extract_fields_from_markdown(content)

            # Update LLM field (remove if set to default)
            default_llm = get_default_llm()
            if llm_name == default_llm or not llm_name:
                # Remove LLM field to use default
                if "LLM" in fields:
                    del fields["LLM"]
            else:
                fields["LLM"] = llm_name

            # Reconstruct markdown file
            lines = []
            for field_name, field_value in fields.items():
                lines.append(f"# {field_name}")
                lines.append(str(field_value).strip())
                lines.append("")

            agent_file.write_text("\n".join(lines), encoding="utf-8")

            # Update agent's LLM in place (don't disconnect client or re-register)
            # LLM changes don't require reconnection to Telegram
            from llm.factory import create_llm_from_name
            agent._llm_name = llm_name if llm_name else None
            agent._llm = create_llm_from_name(agent._llm_name)

            return jsonify({"success": True})
        except Exception as e:
            logger.error(f"Error updating LLM for {agent_config_name}: {e}")
            return jsonify({"error": str(e)}), 500

    @agents_bp.route("/api/agents/<agent_config_name>/configuration/prompt", methods=["PUT"])
    def api_update_agent_prompt(agent_config_name: str):
        """Update agent prompt (instructions)."""
        try:
            agent = get_agent_by_name(agent_config_name)
            if not agent:
                return jsonify({"error": f"Agent '{agent_config_name}' not found"}), 404

            if not agent.config_directory:
                return jsonify({"error": "Agent has no config directory"}), 400

            data = request.json
            prompt = data.get("prompt", "").strip()

            # Find agent's markdown file
            agent_file = Path(agent.config_directory) / "agents" / f"{agent.config_name}.md"
            if not agent_file.exists():
                return jsonify({"error": "Agent configuration file not found"}), 404

            # Read and parse the markdown file
            content = agent_file.read_text(encoding="utf-8")
            from register_agents import extract_fields_from_markdown
            fields = extract_fields_from_markdown(content)

            # Update Agent Instructions field
            fields["Agent Instructions"] = prompt

            # Reconstruct markdown file
            lines = []
            for field_name, field_value in fields.items():
                lines.append(f"# {field_name}")
                lines.append(str(field_value).strip())
                lines.append("")

            agent_file.write_text("\n".join(lines), encoding="utf-8")

            # Update agent's instructions in place (don't disconnect client or re-register)
            # Instruction changes don't require reconnection to Telegram
            agent.instructions = prompt

            return jsonify({"success": True})
        except Exception as e:
            logger.error(f"Error updating prompt for {agent_config_name}: {e}")
            return jsonify({"error": str(e)}), 500

    @agents_bp.route("/api/agents/<agent_config_name>/configuration/timezone", methods=["PUT"])
    def api_update_agent_timezone(agent_config_name: str):
        """Update agent timezone."""
        try:
            agent = get_agent_by_name(agent_config_name)
            if not agent:
                return jsonify({"error": f"Agent '{agent_config_name}' not found"}), 404

            if not agent.config_directory:
                return jsonify({"error": "Agent has no config directory"}), 400

            data = request.json
            timezone = data.get("timezone", "").strip()

            # Find agent's markdown file
            agent_file = Path(agent.config_directory) / "agents" / f"{agent.config_name}.md"
            if not agent_file.exists():
                return jsonify({"error": "Agent configuration file not found"}), 404

            # Read and parse the markdown file
            content = agent_file.read_text(encoding="utf-8")
            from register_agents import extract_fields_from_markdown
            fields = extract_fields_from_markdown(content)

            # Update Agent Timezone field (remove if empty)
            if not timezone:
                # Remove timezone field to use server default
                if "Agent Timezone" in fields:
                    del fields["Agent Timezone"]
            else:
                # Validate timezone before saving
                try:
                    from zoneinfo import ZoneInfo
                    ZoneInfo(timezone)  # Validate it's a valid IANA timezone
                    fields["Agent Timezone"] = timezone
                except Exception as e:
                    return jsonify({"error": f"Invalid timezone: {e}"}), 400

            # Reconstruct markdown file
            lines = []
            for field_name, field_value in fields.items():
                lines.append(f"# {field_name}")
                lines.append(str(field_value).strip())
                lines.append("")

            agent_file.write_text("\n".join(lines), encoding="utf-8")

            # Update agent's timezone in place (don't disconnect client or re-register)
            # Timezone changes don't require reconnection to Telegram
            agent._timezone_raw = timezone if timezone else None
            agent._timezone_normalized = None  # Reset cached normalized timezone to force recalculation

            return jsonify({"success": True})
        except Exception as e:
            logger.error(f"Error updating timezone for {agent_config_name}: {e}")
            return jsonify({"error": str(e)}), 500

    @agents_bp.route("/api/agents/<agent_config_name>/configuration/disabled", methods=["PUT"])
    def api_update_agent_disabled(agent_config_name: str):
        """Update agent disabled status."""
        try:
            agent = get_agent_by_name(agent_config_name)
            if not agent:
                return jsonify({"error": f"Agent '{agent_config_name}' not found"}), 404

            if not agent.config_directory:
                return jsonify({"error": "Agent has no config directory"}), 400

            data = request.json
            is_disabled = data.get("is_disabled", False)

            # Find agent's markdown file
            agent_file = Path(agent.config_directory) / "agents" / f"{agent.config_name}.md"
            if not agent_file.exists():
                return jsonify({"error": "Agent configuration file not found"}), 404

            # Read and parse the markdown file
            content = agent_file.read_text(encoding="utf-8")
            from register_agents import extract_fields_from_markdown
            fields = extract_fields_from_markdown(content)

            # Update Disabled field
            if is_disabled:
                fields["Disabled"] = ""
            else:
                if "Disabled" in fields:
                    del fields["Disabled"]

            # Reconstruct markdown file
            lines = []
            for field_name, field_value in fields.items():
                lines.append(f"# {field_name}")
                if str(field_value).strip():
                    lines.append(str(field_value).strip())
                lines.append("")

            agent_file.write_text("\n".join(lines), encoding="utf-8")

            # Update agent's disabled status in place
            agent.is_disabled = is_disabled

            # If enabling, and the agent loop is running, we might need to start it.
            # For now, we'll just return success and let the user restart if needed,
            # unless we implement dynamic loading.
            
            return jsonify({"success": True})
        except Exception as e:
            logger.error(f"Error updating disabled status for {agent_config_name}: {e}")
            return jsonify({"error": str(e)}), 500

    @agents_bp.route("/api/agents/<agent_config_name>/configuration/rename", methods=["PUT"])
    def api_rename_agent_config(agent_config_name: str):
        """Rename agent config file (only allowed if disabled)."""
        try:
            agent = get_agent_by_name(agent_config_name)
            if not agent:
                return jsonify({"error": f"Agent '{agent_config_name}' not found"}), 404

            if not agent.is_disabled:
                return jsonify({"error": "Agent must be disabled to rename config file"}), 400

            if not agent.config_directory:
                return jsonify({"error": "Agent has no config directory"}), 400

            data = request.json
            new_config_name = data.get("new_config_name", "").strip()
            if not new_config_name:
                return jsonify({"error": "New config name cannot be empty"}), 400

            # Sanitize new_config_name (basic check)
            if not all(c.isalnum() or c in ("-", "_") for c in new_config_name):
                return jsonify({"error": "Invalid characters in config name"}), 400

            # Check if target file already exists
            new_agent_file = Path(agent.config_directory) / "agents" / f"{new_config_name}.md"
            if new_agent_file.exists():
                return jsonify({"error": f"Config file '{new_config_name}.md' already exists"}), 400

            # Current agent file
            old_agent_file = Path(agent.config_directory) / "agents" / f"{agent.config_name}.md"
            if not old_agent_file.exists():
                return jsonify({"error": "Current agent configuration file not found"}), 404

            # Rename file
            old_agent_file.rename(new_agent_file)

            # Update agent config_name in registry
            # This is tricky because the registry uses config_name as key.
            # It's better to force a re-registration of all agents.
            from register_agents import register_all_agents
            from agent import _agent_registry
            
            # Remove old entry from registry
            if agent.config_name in _agent_registry._registry:
                del _agent_registry._registry[agent.config_name]
            
            # Re-register all agents to pick up the rename
            register_all_agents(force=True)

            return jsonify({"success": True, "new_config_name": new_config_name})
        except Exception as e:
            logger.error(f"Error renaming config for {agent_config_name}: {e}")
            return jsonify({"error": str(e)}), 500

    @agents_bp.route("/api/agents/<agent_config_name>", methods=["DELETE"])
    def api_delete_agent(agent_config_name: str):
        """Delete agent (only allowed if disabled)."""
        try:
            agent = get_agent_by_name(agent_config_name)
            if not agent:
                return jsonify({"error": f"Agent '{agent_config_name}' not found"}), 404

            if not agent.is_disabled:
                return jsonify({"error": "Agent must be disabled to delete"}), 400

            data = request.json
            confirmation = data.get("confirmation", "")
            if confirmation != f"DELETE {agent.name}":
                return jsonify({"error": f"Incorrect confirmation string. Expected 'DELETE {agent.name}'"}), 400

            if not agent.config_directory:
                return jsonify({"error": "Agent has no config directory"}), 400

            # 1. Delete agent config file
            config_file = Path(agent.config_directory) / "agents" / f"{agent.config_name}.md"
            if config_file.exists():
                config_file.unlink()
            
            # 2. Delete {configdir}/agents/{agent_name} directory (curated memories etc)
            agent_config_dir = Path(agent.config_directory) / "agents" / agent.config_name
            if agent_config_dir.exists() and agent_config_dir.is_dir():
                import shutil
                shutil.rmtree(agent_config_dir)

            # 3. Delete {statedir}/{agent_name} directory
            from config import STATE_DIRECTORY
            if STATE_DIRECTORY:
                state_dir = Path(STATE_DIRECTORY) / agent.config_name
                if state_dir.exists() and state_dir.is_dir():
                    import shutil
                    shutil.rmtree(state_dir)

            # 4. Delete pending tasks graphs for the agent
            from task_graph import WorkQueue
            wq = WorkQueue.get_instance()
            wq.clear_tasks_for_agent(
                agent_id=agent.agent_id if agent.agent_id else None,
                agent_config_name=agent.config_name,
                agent_display_name=agent.name
            )

            # Remove from registry
            from agent import _agent_registry
            if agent.config_name in _agent_registry._registry:
                del _agent_registry._registry[agent.config_name]

            return jsonify({"success": True})
        except Exception as e:
            logger.error(f"Error deleting agent {agent_config_name}: {e}")
            return jsonify({"error": str(e)}), 500

def register_new_agent_routes(agents_bp: Blueprint):
    """Register routes for creating new agents."""
    
    @agents_bp.route("/api/agents/new", methods=["POST"])
    def api_create_agent():
        """Create a new agent in the specified config directory."""
        try:
            data = request.json
            config_dir = data.get("config_directory")
            if not config_dir:
                from config import CONFIG_DIRECTORIES
                config_dir = CONFIG_DIRECTORIES[0] if CONFIG_DIRECTORIES else None
            
            if not config_dir:
                return jsonify({"error": "No config directory available"}), 400

            # 1. Find a unique name for the new config file
            agents_dir = Path(config_dir) / "agents"
            agents_dir.mkdir(parents=True, exist_ok=True)
            
            base_name = "Untitled"
            config_name = base_name
            counter = 1
            while (agents_dir / f"{config_name}.md").exists():
                config_name = f"{base_name}_{counter}"
                counter += 1
            
            new_file = agents_dir / f"{config_name}.md"

            # 2. Get content from DefaultAgent.md or use default
            default_agent_template = Path(config_dir) / "DefaultAgent.md"
            if default_agent_template.exists():
                content = default_agent_template.read_text(encoding="utf-8")
            else:
                # Fallback content if DefaultAgent.md doesn't exist
                content = "# Agent Name\nUntitled Agent\n\n# Agent Phone\n+1234567890\n\n# Agent Instructions\nYou are a helpful assistant.\n\n# Role Prompt\nPerson\n\n# Disabled\n"
            
            new_file.write_text(content, encoding="utf-8")

            # 3. Force re-registration to pick up the new agent
            from register_agents import register_all_agents
            register_all_agents(force=True)

            return jsonify({"success": True, "config_name": config_name})
        except Exception as e:
            logger.error(f"Error creating new agent: {e}")
            return jsonify({"error": str(e)}), 500
