# agent_server/main.py
#
# Copyright (c) 2025-2026 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.
#
"""Agent server entry point: startup, wiring, and main event loop."""
import asyncio
import logging
import os

from agent import all_agents
from main_loop import set_main_loop
from exceptions import ShutdownException
from register_agents import register_all_agents
from prompt_loader import load_system_prompt
from task_graph import WorkQueue
from admin_console.app import start_admin_console
from admin_console.puppet_master import (
    PuppetMasterUnavailable,
    get_puppet_master_manager,
)
from media.media_scratch import init_media_scratch
from tick import run_tick_loop
from config import (
    GOOGLE_GEMINI_API_KEY,
    GROK_API_KEY,
    OPENAI_API_KEY,
    OPENROUTER_API_KEY,
)

from .auth import authenticate_all_agents
from .loop import run_telegram_loop, periodic_scan

# Ensure handlers are registered (for task graph, etc.)
import handlers  # noqa: F401

# Configure logging level from environment variable, default to INFO
log_level_str = os.getenv("CINDY_LOG_LEVEL", "INFO").upper()
log_level = getattr(logging, log_level_str, logging.INFO)
logging.basicConfig(level=log_level)
logger = logging.getLogger(__name__)

# Suppress verbose telethon.client.updates messages
logging.getLogger("telethon.client.updates").setLevel(logging.WARNING)

STATE_PATH = os.path.join(os.environ["CINDY_AGENT_STATE_DIR"], "work_queue.json")


def _env_flag(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    value = value.strip().lower()
    if value in ("1", "true", "yes", "on"):
        return True
    if value in ("0", "false", "no", "off"):
        return False
    return default


def load_work_queue():
    """Load the work queue singleton (for compatibility, but now uses singleton)."""
    return WorkQueue.get_instance()


async def main():
    # Set the main event loop reference so it can be accessed from anywhere (e.g., Flask routes)
    set_main_loop(asyncio.get_running_loop())

    admin_enabled = _env_flag("CINDY_ADMIN_CONSOLE_ENABLED", True)
    agent_loop_enabled = _env_flag("CINDY_AGENT_LOOP_ENABLED", True)
    admin_host = os.getenv("CINDY_ADMIN_CONSOLE_HOST", "0.0.0.0")
    admin_port_raw = os.getenv("CINDY_ADMIN_CONSOLE_PORT", "5001")
    admin_ssl_cert = os.getenv("CINDY_ADMIN_CONSOLE_SSL_CERT")
    admin_ssl_key = os.getenv("CINDY_ADMIN_CONSOLE_SSL_KEY")

    try:
        admin_port = int(admin_port_raw)
    except ValueError:
        logger.warning(
            "Invalid CINDY_ADMIN_CONSOLE_PORT value %s; defaulting to 5001",
            admin_port_raw,
        )
        admin_port = 5001

    init_media_scratch()
    register_all_agents()

    # Validate API keys for agents' LLM models
    agents_list = list(all_agents())
    missing_keys = []
    for agent in agents_list:
        llm_name = agent._llm_name
        if not llm_name or not llm_name.strip():
            # Default to Gemini, so check for Gemini key
            if not GOOGLE_GEMINI_API_KEY:
                missing_keys.append(f"Agent '{agent.name}' uses default Gemini model but GOOGLE_GEMINI_API_KEY is not set")
        else:
            llm_name_lower = llm_name.strip().lower()
            # Check for OpenRouter format FIRST (before other prefix checks)
            # OpenRouter models use "provider/model" format (e.g., "openai/gpt-oss-120b")
            # This must come before other checks since "openai/gpt-oss-120b" starts with "openai"
            if "/" in llm_name_lower or llm_name_lower.startswith("openrouter"):
                if not OPENROUTER_API_KEY:
                    missing_keys.append(f"Agent '{agent.name}' uses OpenRouter model '{llm_name}' but OPENROUTER_API_KEY is not set")
            elif llm_name_lower.startswith("gemini"):
                if not GOOGLE_GEMINI_API_KEY:
                    missing_keys.append(f"Agent '{agent.name}' uses Gemini model '{llm_name}' but GOOGLE_GEMINI_API_KEY is not set")
            elif llm_name_lower.startswith("grok"):
                if not GROK_API_KEY:
                    missing_keys.append(f"Agent '{agent.name}' uses Grok model '{llm_name}' but GROK_API_KEY is not set")
            elif llm_name_lower.startswith("gpt") or llm_name_lower.startswith("openai"):
                if not OPENAI_API_KEY:
                    missing_keys.append(f"Agent '{agent.name}' uses OpenAI model '{llm_name}' but OPENAI_API_KEY is not set")

    if missing_keys:
        logger.error("Startup validation failed: Missing required API keys for agent LLM models:")
        for error in missing_keys:
            logger.error(f"  - {error}")
        logger.error("Please set the required API keys and restart the server.")
        return

    # Check that Instructions.md can be found in one of the configuration directories
    try:
        load_system_prompt("Instructions")
    except RuntimeError as e:
        logger.error(f"Startup check failed: {e}")
        logger.error("The 'Instructions.md' prompt must be available in one of the configuration directories.")
        logger.error("Make sure your CINDY_AGENT_CONFIG_PATH includes the directory containing 'prompts/Instructions.md'.")
        return

    admin_server = None
    puppet_master_manager = get_puppet_master_manager()

    try:
        if admin_enabled:
            if not puppet_master_manager.is_configured:
                logger.info(
                    "Admin console is disabled because CINDY_PUPPET_MASTER_PHONE is not set."
                )
            else:
                try:
                    puppet_master_manager.ensure_ready(agents_list)
                except PuppetMasterUnavailable as exc:
                    logger.error(
                        "Admin console disabled because puppet master is unavailable: %s",
                        exc,
                    )
                else:
                    admin_server = start_admin_console(
                        admin_host, admin_port,
                        ssl_cert=admin_ssl_cert,
                        ssl_key=admin_ssl_key
                    )

        if not agent_loop_enabled:
            if not admin_enabled:
                logger.info(
                    "CINDY_AGENT_LOOP_ENABLED and CINDY_ADMIN_CONSOLE_ENABLED are both false; exiting."
                )
                return

            if not admin_server:
                logger.error(
                    "Agent loop disabled but admin console failed to start; exiting."
                )
                return

            logger.info(
                "Agent loop disabled via CINDY_AGENT_LOOP_ENABLED; admin console running only."
            )
            try:
                while True:
                    await asyncio.sleep(3600)
            except asyncio.CancelledError:
                logger.info("Shutdown requested; stopping admin console.")
                return

        # Authenticate all agents before starting the tick loop
        auth_success = await authenticate_all_agents(agents_list)
        if not auth_success:
            logger.error("Failed to authenticate any agents, exiting.")
            return

        if admin_enabled and puppet_master_manager.is_configured:
            try:
                puppet_master_manager.ensure_ready(agents_list)
            except PuppetMasterUnavailable as exc:
                logger.error(
                    "Puppet master availability check failed after agent authentication: %s",
                    exc,
                )
                return
        # Now start all the main tasks
        tick_task = asyncio.create_task(
            run_tick_loop(tick_interval_sec=2, state_file_path=STATE_PATH)
        )

        telegram_tasks = [
            asyncio.create_task(run_telegram_loop(agent))
            for agent in all_agents()
        ]

        scan_task = asyncio.create_task(
            periodic_scan(agents_list, interval_sec=10)
        )

        done, pending = await asyncio.wait(
            [tick_task, scan_task, *telegram_tasks],
            return_when=asyncio.FIRST_EXCEPTION,
        )

        for task in pending:
            task.cancel()

        for task in done:
            exc = task.exception()
            if isinstance(exc, ShutdownException):
                logger.info("Shutdown signal received.")
            elif exc:
                raise exc

    finally:
        if admin_server:
            admin_server.shutdown()
        puppet_master_manager.shutdown()
