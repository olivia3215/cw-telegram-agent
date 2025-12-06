# admin_console/agents/configuration.py
#
# Agent configuration management routes for the admin console.

import logging
from pathlib import Path

from flask import Blueprint, jsonify, request  # pyright: ignore[reportMissingImports]

from admin_console.helpers import get_agent_by_name, get_available_llms, get_default_llm

logger = logging.getLogger(__name__)


def register_configuration_routes(agents_bp: Blueprint):
    """Register agent configuration routes."""
    
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

            return jsonify({
                "llm": current_llm,
                "available_llms": available_llms,
                "prompt": agent.instructions,
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

