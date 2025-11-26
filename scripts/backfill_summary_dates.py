#!/usr/bin/env python3
"""
Manual script to backfill summary dates for a specific agent and channel.
Usage: python scripts/backfill_summary_dates.py <agent_name> <channel_id>
"""

import asyncio
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from agent import all_agents
from register_agents import register_all_agents


async def main():
    if len(sys.argv) != 3:
        print("Usage: python scripts/backfill_summary_dates.py <agent_name> <channel_id>")
        sys.exit(1)
    
    agent_name = sys.argv[1]
    channel_id = int(sys.argv[2])
    
    register_all_agents()
    agent = None
    for a in all_agents():
        if a.name == agent_name:
            agent = a
            break
    
    if not agent:
        print(f"Agent '{agent_name}' not found")
        sys.exit(1)
    
    print(f"Backfilling dates for agent '{agent_name}', channel {channel_id}...")
    
    try:
        await agent._storage.backfill_summary_dates(channel_id, agent)
        print("Backfill completed!")
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())

