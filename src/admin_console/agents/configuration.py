# admin_console/agents/configuration.py
#
# Agent configuration management routes for the admin console.

import asyncio
import logging
import math
import shutil
from pathlib import Path

from flask import Blueprint, jsonify, request  # pyright: ignore[reportMissingImports]

from admin_console.helpers import get_agent_by_name, get_available_llms, get_available_timezones, get_default_llm
from config import STATE_DIRECTORY
from prompt_loader import get_available_system_prompts
from utils.markdown import transform_headers_preserving_code_blocks

logger = logging.getLogger(__name__)


def _write_agent_markdown(agent, fields):
    """Reconstruct and write the agent's markdown file."""
    if not agent.config_directory:
        raise ValueError("Agent has no config directory")
    
    agent_file = Path(agent.config_directory) / "agents" / f"{agent.config_name}.md"
    if not agent_file.exists():
        raise FileNotFoundError("Agent configuration file not found")

    lines = []
    for field_name, field_value in fields.items():
        lines.append(f"# {field_name}")
        lines.append("")  # Empty line after header (markdown guideline)
        
        # Handle list or tuple values
        if isinstance(field_value, (list, tuple)):
            for item in field_value:
                if isinstance(item, (list, tuple)):
                    # Handle SET :: STICKER format
                    lines.append(" :: ".join(str(x).strip() for x in item))
                else:
                    lines.append(str(item).strip())
        else:
            val = str(field_value).strip()
            if val:
                lines.append(val)
        
        lines.append("")

    agent_file.write_text("\n".join(lines), encoding="utf-8")


def register_configuration_routes(agents_bp: Blueprint):
    """Register agent configuration routes."""
    register_new_agent_routes(agents_bp)
    
    @agents_bp.route("/api/agents/<agent_config_name>/configuration", methods=["GET"])
    def api_get_agent_configuration(agent_config_name: str):
        """Get agent configuration."""
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
            current_timezone = agent._timezone_raw if agent._timezone_raw else None
            available_timezones = get_available_timezones()
            
            # Get available role prompts
            available_role_prompts = get_available_system_prompts()

            # For explicit stickers, we want a list of "SET :: STICKER" strings
            formatted_explicit_stickers = [f"{s} :: {n}" for s, n in agent.explicit_stickers]

            # Get typing behavior overrides (raw values, can be None)
            start_typing_delay = agent._start_typing_delay if hasattr(agent, '_start_typing_delay') else None
            typing_speed = agent._typing_speed if hasattr(agent, '_typing_speed') else None

            # Get config directory info
            from config import CONFIG_DIRECTORIES
            current_config_directory = agent.config_directory if agent.config_directory else None
            available_config_directories = [{"value": d, "label": d} for d in CONFIG_DIRECTORIES]

            return jsonify({
                "name": agent.name,
                "phone": agent.phone,
                "llm": current_llm,
                "available_llms": available_llms,
                "prompt": agent.instructions,
                "timezone": current_timezone,
                "available_timezones": available_timezones,
                "role_prompt_names": agent.role_prompt_names,
                "available_role_prompts": available_role_prompts,
                "sticker_set_names": agent.sticker_set_names,
                "explicit_stickers": formatted_explicit_stickers,
                "daily_schedule_description": agent.daily_schedule_description if hasattr(agent, 'daily_schedule_description') else None,
                "reset_context_on_first_message": agent.reset_context_on_first_message,
                "is_disabled": agent.is_disabled,
                "start_typing_delay": start_typing_delay,
                "typing_speed": typing_speed,
                "config_directory": current_config_directory,
                "available_config_directories": available_config_directories,
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
                if "LLM" in fields:
                    del fields["LLM"]
            else:
                fields["LLM"] = llm_name

            _write_agent_markdown(agent, fields)

            # Update agent's LLM in place
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
            if not prompt:
                return jsonify({"error": "Agent instructions cannot be empty"}), 400

            # Transform any level 1 headers in instructions to level 2 to maintain
            # proper markdown hierarchy when inserted under "# Agent Instructions"
            # This preserves content inside code blocks correctly
            transformed_prompt = transform_headers_preserving_code_blocks(prompt)

            # Find agent's markdown file
            agent_file = Path(agent.config_directory) / "agents" / f"{agent.config_name}.md"
            if not agent_file.exists():
                return jsonify({"error": "Agent configuration file not found"}), 404

            # Read and parse the markdown file
            content = agent_file.read_text(encoding="utf-8")
            from register_agents import extract_fields_from_markdown
            fields = extract_fields_from_markdown(content)

            # Update Agent Instructions field with transformed prompt
            fields["Agent Instructions"] = transformed_prompt

            _write_agent_markdown(agent, fields)

            # Update agent's instructions in place with transformed version
            agent.instructions = transformed_prompt

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

            # Update Timezone field
            if not timezone or timezone == "None":
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

            _write_agent_markdown(agent, fields)

            # Update agent's timezone in place
            agent._timezone_raw = timezone if timezone and timezone != "None" else None
            agent._timezone_normalized = None

            return jsonify({"success": True})
        except Exception as e:
            logger.error(f"Error updating timezone for {agent_config_name}: {e}")
            return jsonify({"error": str(e)}), 500

    @agents_bp.route("/api/agents/<agent_config_name>/configuration/phone", methods=["PUT"])
    def api_update_agent_phone(agent_config_name: str):
        """Update agent phone number (only allowed if disabled)."""
        try:
            agent = get_agent_by_name(agent_config_name)
            if not agent:
                return jsonify({"error": f"Agent '{agent_config_name}' not found"}), 404

            if not agent.is_disabled:
                return jsonify({"error": "Agent must be disabled to update phone number"}), 400

            data = request.json
            phone = data.get("phone", "").strip()
            if not phone:
                return jsonify({"error": "Phone number cannot be empty"}), 400

            from register_agents import extract_fields_from_markdown
            agent_file = Path(agent.config_directory) / "agents" / f"{agent.config_name}.md"
            content = agent_file.read_text(encoding="utf-8")
            fields = extract_fields_from_markdown(content)
            fields["Agent Phone"] = phone
            _write_agent_markdown(agent, fields)

            agent.phone = phone
            return jsonify({"success": True})
        except Exception as e:
            logger.error(f"Error updating phone for {agent_config_name}: {e}")
            return jsonify({"error": str(e)}), 500

    @agents_bp.route("/api/agents/<agent_config_name>/configuration/name", methods=["PUT"])
    def api_update_agent_name(agent_config_name: str):
        """Update agent display name (only allowed if disabled)."""
        try:
            agent = get_agent_by_name(agent_config_name)
            if not agent:
                return jsonify({"error": f"Agent '{agent_config_name}' not found"}), 404

            if not agent.is_disabled:
                return jsonify({"error": "Agent must be disabled to update name"}), 400

            data = request.json
            name = data.get("name", "").strip()
            if not name:
                return jsonify({"error": "Agent name cannot be empty"}), 400

            from register_agents import extract_fields_from_markdown
            agent_file = Path(agent.config_directory) / "agents" / f"{agent.config_name}.md"
            content = agent_file.read_text(encoding="utf-8")
            fields = extract_fields_from_markdown(content)
            fields["Agent Name"] = name
            _write_agent_markdown(agent, fields)

            agent.name = name
            return jsonify({"success": True})
        except Exception as e:
            logger.error(f"Error updating name for {agent_config_name}: {e}")
            return jsonify({"error": str(e)}), 500

    @agents_bp.route("/api/agents/<agent_config_name>/configuration/role-prompts", methods=["PUT"])
    def api_update_agent_role_prompts(agent_config_name: str):
        """Update agent role prompts (only allowed if disabled)."""
        try:
            agent = get_agent_by_name(agent_config_name)
            if not agent:
                return jsonify({"error": f"Agent '{agent_config_name}' not found"}), 404

            if not agent.is_disabled:
                return jsonify({"error": "Agent must be disabled to update role prompts"}), 400

            data = request.json
            role_prompt_names = data.get("role_prompt_names", [])

            from register_agents import extract_fields_from_markdown
            agent_file = Path(agent.config_directory) / "agents" / f"{agent.config_name}.md"
            content = agent_file.read_text(encoding="utf-8")
            fields = extract_fields_from_markdown(content)
            fields["Role Prompt"] = "\n".join(role_prompt_names)
            _write_agent_markdown(agent, fields)

            agent.role_prompt_names = role_prompt_names
            return jsonify({"success": True})
        except Exception as e:
            logger.error(f"Error updating role prompts for {agent_config_name}: {e}")
            return jsonify({"error": str(e)}), 500

    @agents_bp.route("/api/agents/<agent_config_name>/configuration/stickers", methods=["PUT"])
    def api_update_agent_stickers(agent_config_name: str):
        """Update agent sticker sets and explicit stickers (only allowed if disabled)."""
        try:
            agent = get_agent_by_name(agent_config_name)
            if not agent:
                return jsonify({"error": f"Agent '{agent_config_name}' not found"}), 404

            if not agent.is_disabled:
                return jsonify({"error": "Agent must be disabled to update stickers"}), 400

            data = request.json
            sticker_set_names = data.get("sticker_set_names", [])
            explicit_stickers_raw = data.get("explicit_stickers", [])

            from register_agents import extract_fields_from_markdown, _parse_explicit_stickers
            agent_file = Path(agent.config_directory) / "agents" / f"{agent.config_name}.md"
            content = agent_file.read_text(encoding="utf-8")
            fields = extract_fields_from_markdown(content)
            
            if sticker_set_names:
                fields["Agent Sticker Sets"] = "\n".join(sticker_set_names)
            elif "Agent Sticker Sets" in fields:
                del fields["Agent Sticker Sets"]

            if explicit_stickers_raw:
                fields["Agent Stickers"] = "\n".join(explicit_stickers_raw)
            elif "Agent Stickers" in fields:
                del fields["Agent Stickers"]

            _write_agent_markdown(agent, fields)

            agent.sticker_set_names = sticker_set_names
            agent.explicit_stickers = _parse_explicit_stickers(explicit_stickers_raw)
            return jsonify({"success": True})
        except Exception as e:
            logger.error(f"Error updating stickers for {agent_config_name}: {e}")
            return jsonify({"error": str(e)}), 500

    @agents_bp.route("/api/agents/<agent_config_name>/configuration/daily-schedule", methods=["PUT"])
    def api_update_agent_daily_schedule(agent_config_name: str):
        """Update agent daily schedule (only allowed if disabled)."""
        try:
            agent = get_agent_by_name(agent_config_name)
            if not agent:
                return jsonify({"error": f"Agent '{agent_config_name}' not found"}), 404

            if not agent.is_disabled:
                return jsonify({"error": "Agent must be disabled to update daily schedule"}), 400

            data = request.json
            enabled = data.get("enabled", False)
            description = data.get("description", "").strip()

            from register_agents import extract_fields_from_markdown
            agent_file = Path(agent.config_directory) / "agents" / f"{agent.config_name}.md"
            content = agent_file.read_text(encoding="utf-8")
            fields = extract_fields_from_markdown(content)
            
            if enabled:
                fields["Daily Schedule"] = description
            else:
                if "Daily Schedule" in fields:
                    del fields["Daily Schedule"]

            _write_agent_markdown(agent, fields)

            agent.daily_schedule_description = description if enabled else None
            return jsonify({"success": True})
        except Exception as e:
            logger.error(f"Error updating daily schedule for {agent_config_name}: {e}")
            return jsonify({"error": str(e)}), 500

    @agents_bp.route("/api/agents/<agent_config_name>/configuration/reset-context", methods=["PUT"])
    def api_update_agent_reset_context(agent_config_name: str):
        """Update agent reset context on first message (only allowed if disabled)."""
        try:
            agent = get_agent_by_name(agent_config_name)
            if not agent:
                return jsonify({"error": f"Agent '{agent_config_name}' not found"}), 404

            if not agent.is_disabled:
                return jsonify({"error": "Agent must be disabled to update reset context"}), 400

            data = request.json
            reset_context = data.get("reset_context_on_first_message", False)

            from register_agents import extract_fields_from_markdown
            agent_file = Path(agent.config_directory) / "agents" / f"{agent.config_name}.md"
            content = agent_file.read_text(encoding="utf-8")
            fields = extract_fields_from_markdown(content)
            
            if reset_context:
                fields["Reset Context On First Message"] = ""
            else:
                if "Reset Context On First Message" in fields:
                    del fields["Reset Context On First Message"]

            _write_agent_markdown(agent, fields)

            agent.reset_context_on_first_message = reset_context
            return jsonify({"success": True})
        except Exception as e:
            logger.error(f"Error updating reset context for {agent_config_name}: {e}")
            return jsonify({"error": str(e)}), 500

    @agents_bp.route("/api/agents/<agent_config_name>/configuration/start-typing-delay", methods=["PUT"])
    def api_update_agent_start_typing_delay(agent_config_name: str):
        """Update agent start typing delay override."""
        try:
            agent = get_agent_by_name(agent_config_name)
            if not agent:
                return jsonify({"error": f"Agent '{agent_config_name}' not found"}), 404

            if not agent.config_directory:
                return jsonify({"error": "Agent has no config directory"}), 400

            data = request.json
            start_typing_delay_str = (data.get("start_typing_delay") or "").strip()

            # Find agent's markdown file
            agent_file = Path(agent.config_directory) / "agents" / f"{agent.config_name}.md"
            if not agent_file.exists():
                return jsonify({"error": "Agent configuration file not found"}), 404

            # Read and parse the markdown file
            content = agent_file.read_text(encoding="utf-8")
            from register_agents import extract_fields_from_markdown
            fields = extract_fields_from_markdown(content)

            # Update Start Typing Delay field
            if not start_typing_delay_str or start_typing_delay_str == "None":
                if "Start Typing Delay" in fields:
                    del fields["Start Typing Delay"]
                start_typing_delay_value = None
            else:
                # Validate value
                try:
                    start_typing_delay_value = float(start_typing_delay_str)
                    if not math.isfinite(start_typing_delay_value):
                        return jsonify({"error": "Start Typing Delay must be a finite number (NaN and infinity are not allowed)"}), 400
                    if start_typing_delay_value < 1 or start_typing_delay_value > 3600:
                        return jsonify({"error": "Start Typing Delay must be between 1 and 3600 seconds"}), 400
                    fields["Start Typing Delay"] = str(start_typing_delay_value)
                except ValueError:
                    return jsonify({"error": "Invalid Start Typing Delay value (must be a number)"}), 400

            _write_agent_markdown(agent, fields)

            # Update agent's start typing delay in place
            agent._start_typing_delay = start_typing_delay_value

            return jsonify({"success": True})
        except Exception as e:
            logger.error(f"Error updating start typing delay for {agent_config_name}: {e}")
            return jsonify({"error": str(e)}), 500

    @agents_bp.route("/api/agents/<agent_config_name>/configuration/typing-speed", methods=["PUT"])
    def api_update_agent_typing_speed(agent_config_name: str):
        """Update agent typing speed override."""
        try:
            agent = get_agent_by_name(agent_config_name)
            if not agent:
                return jsonify({"error": f"Agent '{agent_config_name}' not found"}), 404

            if not agent.config_directory:
                return jsonify({"error": "Agent has no config directory"}), 400

            data = request.json
            typing_speed_str = (data.get("typing_speed") or "").strip()

            # Find agent's markdown file
            agent_file = Path(agent.config_directory) / "agents" / f"{agent.config_name}.md"
            if not agent_file.exists():
                return jsonify({"error": "Agent configuration file not found"}), 404

            # Read and parse the markdown file
            content = agent_file.read_text(encoding="utf-8")
            from register_agents import extract_fields_from_markdown
            fields = extract_fields_from_markdown(content)

            # Update Typing Speed field
            if not typing_speed_str or typing_speed_str == "None":
                if "Typing Speed" in fields:
                    del fields["Typing Speed"]
                typing_speed_value = None
            else:
                # Validate value
                try:
                    typing_speed_value = float(typing_speed_str)
                    if not math.isfinite(typing_speed_value):
                        return jsonify({"error": "Typing Speed must be a finite number (NaN and infinity are not allowed)"}), 400
                    if typing_speed_value < 1 or typing_speed_value > 1000:
                        return jsonify({"error": "Typing Speed must be between 1 and 1000 characters per second"}), 400
                    fields["Typing Speed"] = str(typing_speed_value)
                except ValueError:
                    return jsonify({"error": "Invalid Typing Speed value (must be a number)"}), 400

            _write_agent_markdown(agent, fields)

            # Update agent's typing speed in place
            agent._typing_speed = typing_speed_value

            return jsonify({"success": True})
        except Exception as e:
            logger.error(f"Error updating typing speed for {agent_config_name}: {e}")
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

            _write_agent_markdown(agent, fields)

            # Update agent's disabled status in place
            agent.is_disabled = is_disabled

            # If agent is being disabled, disconnect its client to release the SQLite session lock
            if is_disabled and agent.client:
                try:
                    from main_loop import get_main_loop
                    
                    # Store reference to client before clearing it synchronously
                    # This ensures that if the agent is re-enabled immediately, the check
                    # for agent.client will see None and start run_telegram_loop
                    client_to_disconnect = agent._client
                    
                    # Clear client reference immediately (synchronously) so that
                    # subsequent enable checks see None and can start run_telegram_loop
                    agent.clear_client_and_caches()
                    
                    main_loop = get_main_loop()
                    if main_loop and main_loop.is_running():
                        # Schedule disconnection in the main event loop
                        async def disconnect_client():
                            try:
                                if client_to_disconnect and client_to_disconnect.is_connected():
                                    await client_to_disconnect.disconnect()
                                    logger.info(f"Disconnected client for disabled agent {agent_config_name}")
                            except Exception as e:
                                logger.warning(f"Error disconnecting client for disabled agent {agent_config_name}: {e}")
                        
                        def schedule_disconnect():
                            try:
                                main_loop.create_task(disconnect_client())
                            except Exception as e:
                                logger.warning(f"Error scheduling client disconnection for disabled agent {agent_config_name}: {e}")
                        
                        main_loop.call_soon_threadsafe(schedule_disconnect)
                        logger.info(f"Scheduled client disconnection for disabled agent {agent_config_name}")
                    else:
                        # No main loop found - run_telegram_loop will handle disconnection when it sees is_disabled
                        logger.info(f"Agent {agent_config_name} disabled - client will be disconnected when run_telegram_loop checks is_disabled")
                except Exception as e:
                    logger.warning(f"Error scheduling client disconnection for disabled agent {agent_config_name}: {e}")
                    # Don't fail the request - run_telegram_loop will handle disconnection when it sees is_disabled

            # If agent is being enabled and doesn't have a client, try to start run_telegram_loop for it
            # This ensures the agent gets authenticated and its client is available
            # Note: We only start run_telegram_loop if the agent doesn't already have a client,
            # to avoid "database is locked" errors from concurrent SQLite access
            if not is_disabled and not agent.client:
                try:
                    from main_loop import get_main_loop
                    from run import run_telegram_loop
                    
                    main_loop = get_main_loop()
                    if main_loop and main_loop.is_running():
                        # Schedule run_telegram_loop on the main loop
                        # run_telegram_loop will handle authentication internally
                        def schedule_loop():
                            # Double-check agent still doesn't have a client before starting
                            # This avoids "database is locked" errors if client was created elsewhere
                            if not agent.client:
                                try:
                                    # Use loop.create_task() instead of asyncio.create_task()
                                    # because we're in a synchronous callback scheduled on the loop
                                    main_loop.create_task(run_telegram_loop(agent))
                                    logger.info(f"Scheduled run_telegram_loop for {agent_config_name}")
                                except Exception as e:
                                    error_msg = str(e).lower()
                                    if "database is locked" in error_msg or "locked" in error_msg:
                                        logger.warning(
                                            f"Agent {agent_config_name} session file is locked when starting run_telegram_loop. "
                                            "This usually means the agent is already authenticated. The agent is enabled and should work normally."
                                        )
                                    else:
                                        logger.error(f"Error starting run_telegram_loop for {agent_config_name}: {e}")
                        
                        main_loop.call_soon_threadsafe(schedule_loop)
                    else:
                        # No main loop available - log that the agent needs to be started on restart
                        logger.info(
                            f"Agent {agent_config_name} enabled but main event loop is not available. "
                            "run_telegram_loop will start on next system restart, or use the login endpoint to authenticate manually."
                        )
                except Exception as e:
                    error_msg = str(e).lower()
                    if "database is locked" in error_msg or "locked" in error_msg:
                        logger.warning(
                            f"Agent {agent_config_name} session file is locked when enabling. "
                            "This usually means the agent is already authenticated. The agent is enabled and should work normally."
                        )
                    else:
                        logger.error(f"Error setting up agent {agent_config_name} after enabling: {e}")
                    # Don't fail the request - the agent is enabled, authentication can happen later

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

            # Check if target directories already exist
            old_config_name = agent.config_name
            
            # Check state directory
            if STATE_DIRECTORY:
                old_state_dir = Path(STATE_DIRECTORY) / old_config_name
                new_state_dir = Path(STATE_DIRECTORY) / new_config_name
                if new_state_dir.exists():
                    return jsonify({"error": f"State directory '{new_config_name}' already exists"}), 400

            # Check config directory
            old_agent_config_dir = Path(agent.config_directory) / "agents" / old_config_name
            new_agent_config_dir = Path(agent.config_directory) / "agents" / new_config_name
            if new_agent_config_dir.exists():
                return jsonify({"error": f"Config directory '{new_config_name}' already exists"}), 400

            # Rename directories first (before renaming the config file)
            # Track which directories were successfully renamed so we can rollback if file rename fails
            state_dir_renamed = False
            config_dir_renamed = False
            try:
                # Rename state directory if it exists
                if STATE_DIRECTORY:
                    old_state_dir = Path(STATE_DIRECTORY) / old_config_name
                    new_state_dir = Path(STATE_DIRECTORY) / new_config_name
                    if old_state_dir.exists() and old_state_dir.is_dir():
                        shutil.move(str(old_state_dir), str(new_state_dir))
                        state_dir_renamed = True
                        logger.info(f"Renamed state directory from {old_state_dir} to {new_state_dir}")

                # Rename config directory if it exists
                if old_agent_config_dir.exists() and old_agent_config_dir.is_dir():
                    shutil.move(str(old_agent_config_dir), str(new_agent_config_dir))
                    config_dir_renamed = True
                    logger.info(f"Renamed config directory from {old_agent_config_dir} to {new_agent_config_dir}")
            except Exception as e:
                logger.error(f"Error renaming directories for {old_config_name}: {e}")
                # Rollback directories if they were already renamed
                rollback_errors = []
                if state_dir_renamed and STATE_DIRECTORY:
                    try:
                        old_state_dir = Path(STATE_DIRECTORY) / old_config_name
                        new_state_dir = Path(STATE_DIRECTORY) / new_config_name
                        if new_state_dir.exists() and new_state_dir.is_dir():
                            shutil.move(str(new_state_dir), str(old_state_dir))
                            logger.info(f"Rolled back state directory rename from {new_state_dir} to {old_state_dir}")
                    except Exception as rollback_e:
                        rollback_errors.append(f"Failed to rollback state directory: {rollback_e}")
                        logger.error(f"Failed to rollback state directory rename: {rollback_e}")
                
                try:
                    if config_dir_renamed:
                        if new_agent_config_dir.exists() and new_agent_config_dir.is_dir():
                            shutil.move(str(new_agent_config_dir), str(old_agent_config_dir))
                            logger.info(f"Rolled back config directory rename from {new_agent_config_dir} to {old_agent_config_dir}")
                except Exception as rollback_e:
                    rollback_errors.append(f"Failed to rollback config directory: {rollback_e}")
                    logger.error(f"Failed to rollback config directory rename: {rollback_e}")
                
                error_msg = f"Failed to rename directories: {e}"
                if rollback_errors:
                    error_msg += f". Additionally, rollback errors occurred: {'; '.join(rollback_errors)}"
                return jsonify({"error": error_msg}), 500

            # Rename file - if this fails, rollback the directory renames
            try:
                old_agent_file.rename(new_agent_file)
            except Exception as e:
                logger.error(f"Error renaming config file for {old_config_name}: {e}")
                # Rollback directory renames
                rollback_errors = []
                try:
                    if state_dir_renamed and STATE_DIRECTORY:
                        old_state_dir = Path(STATE_DIRECTORY) / old_config_name
                        new_state_dir = Path(STATE_DIRECTORY) / new_config_name
                        if new_state_dir.exists() and new_state_dir.is_dir():
                            shutil.move(str(new_state_dir), str(old_state_dir))
                            logger.info(f"Rolled back state directory rename from {new_state_dir} to {old_state_dir}")
                except Exception as rollback_e:
                    rollback_errors.append(f"Failed to rollback state directory: {rollback_e}")
                    logger.error(f"Failed to rollback state directory rename: {rollback_e}")
                
                try:
                    if config_dir_renamed:
                        if new_agent_config_dir.exists() and new_agent_config_dir.is_dir():
                            shutil.move(str(new_agent_config_dir), str(old_agent_config_dir))
                            logger.info(f"Rolled back config directory rename from {new_agent_config_dir} to {old_agent_config_dir}")
                except Exception as rollback_e:
                    rollback_errors.append(f"Failed to rollback config directory: {rollback_e}")
                    logger.error(f"Failed to rollback config directory rename: {rollback_e}")
                
                error_msg = f"Failed to rename config file: {e}"
                if rollback_errors:
                    error_msg += f". Additionally, rollback errors occurred: {'; '.join(rollback_errors)}"
                return jsonify({"error": error_msg}), 500

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

    @agents_bp.route("/api/agents/<agent_config_name>/configuration/move-directory", methods=["PUT"])
    def api_move_agent_config_directory(agent_config_name: str):
        """Move agent config directory (only allowed if disabled)."""
        try:
            agent = get_agent_by_name(agent_config_name)
            if not agent:
                return jsonify({"error": f"Agent '{agent_config_name}' not found"}), 404

            if not agent.is_disabled:
                return jsonify({"error": "Agent must be disabled to move config directory"}), 400

            if not agent.config_directory:
                return jsonify({"error": "Agent has no config directory"}), 400

            data = request.json
            new_config_directory = data.get("config_directory", "").strip()
            if not new_config_directory:
                return jsonify({"error": "New config directory cannot be empty"}), 400

            # Validate new config directory is in allowed list
            from config import CONFIG_DIRECTORIES
            if new_config_directory not in CONFIG_DIRECTORIES:
                return jsonify({"error": f"Invalid config directory. Must be one of: {', '.join(CONFIG_DIRECTORIES)}"}), 400

            # Check if we're already in the target directory
            if agent.config_directory == new_config_directory:
                return jsonify({"error": "Agent is already in the specified config directory"}), 400

            old_config_dir = Path(agent.config_directory)
            new_config_dir = Path(new_config_directory)

            # Validate directories exist
            if not old_config_dir.exists() or not old_config_dir.is_dir():
                return jsonify({"error": f"Old config directory does not exist: {agent.config_directory}"}), 400
            if not new_config_dir.exists() or not new_config_dir.is_dir():
                return jsonify({"error": f"New config directory does not exist: {new_config_directory}"}), 400

            # Ensure agents subdirectory exists in new config directory
            new_agents_dir = new_config_dir / "agents"
            new_agents_dir.mkdir(parents=True, exist_ok=True)

            # Check if target already has an agent with the same config_name
            new_agent_file = new_agents_dir / f"{agent.config_name}.md"
            if new_agent_file.exists():
                return jsonify({"error": f"Agent config file '{agent.config_name}.md' already exists in target directory"}), 400

            new_agent_config_dir = new_agents_dir / agent.config_name
            if new_agent_config_dir.exists():
                return jsonify({"error": f"Agent config directory '{agent.config_name}' already exists in target directory"}), 400

            # Get paths for old files/directories
            old_agents_dir = old_config_dir / "agents"
            old_agent_file = old_agents_dir / f"{agent.config_name}.md"
            old_agent_config_dir = old_agents_dir / agent.config_name

            # Validate old files/directories exist
            if not old_agent_file.exists():
                return jsonify({"error": "Agent configuration file not found"}), 404

            # Move the agent config file
            try:
                shutil.move(str(old_agent_file), str(new_agent_file))
                logger.info(f"Moved agent config file from {old_agent_file} to {new_agent_file}")
            except Exception as e:
                logger.error(f"Error moving agent config file: {e}")
                return jsonify({"error": f"Failed to move agent config file: {e}"}), 500

            # Move the agent config directory (if it exists)
            if old_agent_config_dir.exists() and old_agent_config_dir.is_dir():
                try:
                    shutil.move(str(old_agent_config_dir), str(new_agent_config_dir))
                    logger.info(f"Moved agent config directory from {old_agent_config_dir} to {new_agent_config_dir}")
                except Exception as e:
                    logger.error(f"Error moving agent config directory: {e}")
                    # Try to rollback the file move
                    rollback_errors = []
                    try:
                        shutil.move(str(new_agent_file), str(old_agent_file))
                        logger.info(f"Rolled back file move from {new_agent_file} to {old_agent_file}")
                    except Exception as rollback_e:
                        rollback_errors.append(f"Failed to rollback file move: {rollback_e}")
                        logger.error(f"Failed to rollback file move: {rollback_e}")
                    
                    error_msg = f"Failed to move agent config directory: {e}"
                    if rollback_errors:
                        error_msg += f". Additionally, rollback errors occurred: {'; '.join(rollback_errors)}"
                    return jsonify({"error": error_msg}), 500

            # Re-register all agents to pick up the move
            # This clears the registry and creates new agent objects, so the new config_directory
            # will be read from the moved config file during re-registration
            from register_agents import register_all_agents
            register_all_agents(force=True)

            return jsonify({"success": True, "new_config_directory": new_config_directory})
        except Exception as e:
            logger.error(f"Error moving config directory for {agent_config_name}: {e}")
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

            # 0. Read Telegram ID from config file BEFORE deleting it
            # (since agent is disabled, we can't use client to get it)
            config_file = Path(agent.config_directory) / "agents" / f"{agent.config_name}.md"
            telegram_id = None
            if config_file.exists():
                from register_agents import get_agent_telegram_id_from_config
                telegram_id = get_agent_telegram_id_from_config(config_file)

            # 1. Delete agent config file
            if config_file.exists():
                config_file.unlink()
            
            # 2. Delete {configdir}/agents/{agent_name} directory (notes etc)
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

            # 5. Delete MySQL data for the agent
            if telegram_id:
                try:
                    from db.agent_deletion import delete_all_agent_data
                    deleted_counts = delete_all_agent_data(telegram_id)
                    logger.info(
                        f"Deleted MySQL data for agent {agent.name} (telegram_id={telegram_id}): "
                        f"{deleted_counts}"
                    )
                except Exception as e:
                    logger.error(
                        f"Failed to delete MySQL data for agent {agent.name} "
                        f"(telegram_id={telegram_id}): {e}"
                    )
                    # Don't fail the deletion if MySQL cleanup fails
            else:
                logger.warning(
                    f"Could not determine Telegram ID for agent {agent.name} to delete MySQL data. "
                    f"MySQL data may remain in the database."
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
                content = "# Agent Name\n\nUntitled Agent\n\n# Agent Phone\n\n+1234567890\n\n# Agent Instructions\n\nYou are a helpful assistant.\n\n# Role Prompt\n\nPerson\n\n# Disabled\n\n"
            
            new_file.write_text(content, encoding="utf-8")

            # 3. Force re-registration to pick up the new agent
            from register_agents import register_all_agents
            register_all_agents(force=True)

            return jsonify({"success": True, "config_name": config_name})
        except Exception as e:
            logger.error(f"Error creating new agent: {e}")
            return jsonify({"error": str(e)}), 500
