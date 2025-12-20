#!/usr/bin/env python3
# remove_synced_contacts.py
#
# Copyright (c) 2025 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.
#
# Script to remove synced phone contacts from Telegram.
# This removes contacts who don't have Telegram accounts, preventing
# notifications when they sign up for Telegram in the future.

import argparse
import asyncio
import logging
import sys
from pathlib import Path

# Add src directory to path so we can import modules
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from telethon.tl.functions.contacts import ResetSavedRequest  # pyright: ignore[reportMissingImports]

from agent import all_agents
from config import PUPPET_MASTER_PHONE
from register_agents import register_all_agents
from telegram_util import get_puppet_master_client, get_telegram_client

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


async def remove_synced_contacts_for_agent(agent_name: str, phone: str) -> bool:
    """
    Remove all synced phone contacts for a specific agent.
    
    This removes contacts who don't have Telegram accounts from the synced contact list,
    preventing notifications when they sign up for Telegram.
    
    Returns True if successful, False otherwise.
    """
    client = get_telegram_client(agent_name, phone)
    
    try:
        await client.connect()
        
        if not await client.is_user_authorized():
            logger.error(f"[{agent_name}] Not authenticated. Please run './telegram_login.sh' first.")
            return False
        
        me = await client.get_me()
        logger.info(f"[{agent_name}] Connected as: {me.username or me.first_name} ({me.id})")
        
        logger.info(f"[{agent_name}] Removing all synced phone contacts...")
        result = await client(ResetSavedRequest())
        
        logger.info(f"[{agent_name}] Successfully removed synced contacts.")
        logger.info(f"[{agent_name}] Result: {result}")
        
        return True
        
    except Exception as e:
        logger.exception(f"[{agent_name}] Error removing synced contacts: {e}")
        return False
    finally:
        await client.disconnect()


# async def remove_synced_contacts_for_puppet_master() -> bool:
#     """Remove synced contacts for the puppet master account."""
#     if not PUPPET_MASTER_PHONE:
#         logger.error("CINDY_PUPPET_MASTER_PHONE is not set.")
#         return False
    
#     client = get_puppet_master_client()
    
#     try:
#         await client.connect()
        
#         if not await client.is_user_authorized():
#             logger.error("Puppet master not authenticated. Please run './telegram_login.sh --puppet-master' first.")
#             return False
        
#         me = await client.get_me()
#         logger.info(f"Puppet master connected as: {me.username or me.first_name} ({me.id})")
        
#         logger.info("Removing all synced phone contacts for puppet master...")
#         result = await client(ResetSavedRequest())
        
#         logger.info("Successfully removed synced contacts for puppet master.")
#         logger.info(f"Result: {result}")
        
#         return True
        
#     except Exception as e:
#         logger.exception(f"Error removing synced contacts for puppet master: {e}")
#         return False
#     finally:
#         await client.disconnect()


async def async_main(args: argparse.Namespace) -> int:
    """Main async function."""
    # if args.puppet_master:
    #     success = await remove_synced_contacts_for_puppet_master()
    #     return 0 if success else 1
    
    if args.agent:
        # Find the specified agent
        register_all_agents()
        agent = None
        for a in all_agents(include_disabled=True):
            if a.name == args.agent:
                agent = a
                break
        
        if not agent:
            logger.error(f"Agent '{args.agent}' not found.")
            return 1
        
        success = await remove_synced_contacts_for_agent(agent.name, agent.phone)
        return 0 if success else 1
    
    # Default: process all agents
    register_all_agents()
    agents = list(all_agents(include_disabled=True))
    
    if not agents:
        logger.error("No agents found. Please register agents first.")
        return 1
    
    logger.info(f"Removing synced contacts for {len(agents)} agent(s)...")
    
    results = []
    for agent in agents:
        success = await remove_synced_contacts_for_agent(agent.name, agent.phone)
        results.append(success)
    
    all_success = all(results)
    if all_success:
        logger.info("Successfully removed synced contacts for all agents.")
    else:
        logger.warning("Some agents failed to remove synced contacts.")
    
    return 0 if all_success else 1


def parse_args() -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Remove synced phone contacts from Telegram. "
                    "This removes contacts who don't have Telegram accounts, "
                    "preventing notifications when they sign up."
    )
    # parser.add_argument(
    #     "--puppet-master",
    #     action="store_true",
    #     help="Remove synced contacts for the puppet master account instead of agents.",
    # )
    parser.add_argument(
        "--agent",
        type=str,
        help="Remove synced contacts for a specific agent by name.",
    )
    return parser.parse_args()


def main() -> int:
    """Main entry point."""
    args = parse_args()
    return asyncio.run(async_main(args))


if __name__ == "__main__":
    raise SystemExit(main())

