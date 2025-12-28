#!/usr/bin/env python3
"""
Migration script to move data from filesystem to MySQL.

This script reads all JSON files from statedir and migrates them to MySQL.
It supports dry-run mode, resume capability, and verification.
"""

import argparse
import asyncio
import hashlib
import json
import logging
import sys
from datetime import datetime
from pathlib import Path

# Add src to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root / "src"))

from config import STATE_DIRECTORY
from db import (
    agent_activity,
    intentions,
    memories,
    plans,
    schedules,
    summaries,
    translations as db_translations,
)
from db.connection import get_db_connection
from db.schema import create_schema
from db import media_metadata

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)


def _hash_message(message: str) -> bytes:
    """Calculate 128-bit hash of a message."""
    return hashlib.md5(message.encode("utf-8")).digest()


def migrate_memories(agent_config_name: str, agent_telegram_id: int, dry_run: bool = False) -> int:
    """Migrate memories from filesystem to MySQL."""
    memory_file = Path(STATE_DIRECTORY) / agent_config_name / "memory.json"
    if not memory_file.exists():
        return 0
    
    try:
        from memory_storage import load_property_entries
        
        memories_list, _ = load_property_entries(
            memory_file, "memory", default_id_prefix="memory"
        )
        
        count = 0
        for memory in memories_list:
            if dry_run:
                logger.info(f"  [DRY RUN] Would migrate memory: {memory.get('id')}")
                count += 1
            else:
                try:
                    memories.save_memory(
                        agent_telegram_id=agent_telegram_id,
                        memory_id=memory.get("id"),
                        content=memory.get("content", ""),
                        created=memory.get("created"),
                        creation_channel=memory.get("creation_channel"),
                        creation_channel_id=memory.get("creation_channel_id"),
                        creation_channel_username=memory.get("creation_channel_username"),
                        metadata=memory,
                    )
                    count += 1
                except Exception as e:
                    logger.error(f"  Failed to migrate memory {memory.get('id')}: {e}")
        
        return count
    except Exception as e:
        logger.error(f"Failed to migrate memories for {agent_config_name}: {e}")
        return 0


def migrate_intentions(agent_config_name: str, agent_telegram_id: int, dry_run: bool = False) -> int:
    """Migrate intentions from filesystem to MySQL."""
    memory_file = Path(STATE_DIRECTORY) / agent_config_name / "memory.json"
    if not memory_file.exists():
        return 0
    
    try:
        from memory_storage import load_property_entries
        
        intentions_list, _ = load_property_entries(
            memory_file, "intention", default_id_prefix="intent"
        )
        
        count = 0
        for intention in intentions_list:
            if dry_run:
                logger.info(f"  [DRY RUN] Would migrate intention: {intention.get('id')}")
                count += 1
            else:
                try:
                    intentions.save_intention(
                        agent_telegram_id=agent_telegram_id,
                        intention_id=intention.get("id"),
                        content=intention.get("content", ""),
                        created=intention.get("created"),
                        metadata=intention,
                    )
                    count += 1
                except Exception as e:
                    logger.error(f"  Failed to migrate intention {intention.get('id')}: {e}")
        
        return count
    except Exception as e:
        logger.error(f"Failed to migrate intentions for {agent_config_name}: {e}")
        return 0


def migrate_plans(agent_config_name: str, agent_telegram_id: int, dry_run: bool = False) -> int:
    """Migrate plans from filesystem to MySQL."""
    memory_dir = Path(STATE_DIRECTORY) / agent_config_name / "memory"
    if not memory_dir.exists():
        return 0
    
    count = 0
    for channel_file in memory_dir.glob("*.json"):
        try:
            channel_id = int(channel_file.stem)
        except ValueError:
            continue  # Skip non-numeric channel IDs
        
        try:
            from memory_storage import load_property_entries
            
            plans_list, _ = load_property_entries(
                channel_file, "plan", default_id_prefix="plan"
            )
            
            for plan in plans_list:
                if dry_run:
                    logger.info(f"  [DRY RUN] Would migrate plan: {plan.get('id')} for channel {channel_id}")
                    count += 1
                else:
                    try:
                        plans.save_plan(
                            agent_telegram_id=agent_telegram_id,
                            channel_id=channel_id,
                            plan_id=plan.get("id"),
                            content=plan.get("content", ""),
                            created=plan.get("created"),
                            metadata=plan,
                        )
                        count += 1
                    except Exception as e:
                        logger.error(f"  Failed to migrate plan {plan.get('id')}: {e}")
        except Exception as e:
            logger.error(f"Failed to migrate plans from {channel_file}: {e}")
    
    return count


def migrate_summaries(agent_config_name: str, agent_telegram_id: int, dry_run: bool = False, verbose: bool = False) -> int:
    """Migrate summaries from filesystem to MySQL."""
    memory_dir = Path(STATE_DIRECTORY) / agent_config_name / "memory"
    if not memory_dir.exists():
        if verbose:
            logger.debug(f"  No memory directory found: {memory_dir}")
        return 0
    
    count = 0
    channel_files = list(memory_dir.glob("*.json"))
    if verbose:
        logger.info(f"  Found {len(channel_files)} channel files in {memory_dir}")
    
    for channel_file in channel_files:
        try:
            channel_id = int(channel_file.stem)
        except ValueError:
            if verbose:
                logger.debug(f"  Skipping non-numeric channel file: {channel_file.name}")
            continue  # Skip non-numeric channel IDs
        
        try:
            from memory_storage import load_property_entries
            
            summaries_list, _ = load_property_entries(
                channel_file, "summary", default_id_prefix="summary"
            )
            
            if verbose:
                logger.info(f"  Processing channel {channel_id}: found {len(summaries_list)} summaries")
            
            for summary in summaries_list:
                summary_id = summary.get("id", "unknown")
                if dry_run:
                    logger.info(f"  [DRY RUN] Would migrate summary: {summary_id} for channel {channel_id}")
                    count += 1
                else:
                    try:
                        if verbose:
                            content_preview = summary.get("content", "")[:50] if summary.get("content") else "(empty)"
                            logger.debug(f"    Migrating summary {summary_id}: content length={len(summary.get('content', ''))}, preview='{content_preview}...'")
                        
                        summaries.save_summary(
                            agent_telegram_id=agent_telegram_id,
                            channel_id=channel_id,
                            summary_id=summary_id,
                            content=summary.get("content", ""),
                            min_message_id=summary.get("min_message_id"),
                            max_message_id=summary.get("max_message_id"),
                            first_message_date=summary.get("first_message_date"),
                            last_message_date=summary.get("last_message_date"),
                            created=summary.get("created"),
                            metadata=summary,
                        )
                        if verbose:
                            logger.debug(f"    ✓ Successfully migrated summary {summary_id} for channel {channel_id}")
                        count += 1
                    except Exception as e:
                        logger.error(f"  Failed to migrate summary {summary_id} for channel {channel_id}: {e}")
                        logger.exception("  Full traceback:")
        except Exception as e:
            logger.error(f"Failed to migrate summaries from {channel_file}: {e}")
            logger.exception("  Full traceback:")
    
    if verbose:
        logger.info(f"  Migrated {count} summaries for agent {agent_config_name}")
    return count


def migrate_schedules(agent_config_name: str, agent_telegram_id: int, dry_run: bool = False) -> int:
    """Migrate schedules from filesystem to MySQL."""
    schedule_file = Path(STATE_DIRECTORY) / agent_config_name / "schedule.json"
    if not schedule_file.exists():
        return 0
    
    try:
        with open(schedule_file, "r", encoding="utf-8") as f:
            schedule = json.load(f)
        
        if dry_run:
            logger.info(f"  [DRY RUN] Would migrate schedule for {agent_config_name}")
            return 1
        else:
            try:
                schedules.save_schedule(agent_telegram_id, schedule)
                return 1
            except Exception as e:
                logger.error(f"  Failed to migrate schedule: {e}")
                return 0
    except Exception as e:
        logger.error(f"Failed to migrate schedule for {agent_config_name}: {e}")
        return 0


def migrate_translations(dry_run: bool = False) -> int:
    """Migrate translations from filesystem to MySQL."""
    translations_file = Path(STATE_DIRECTORY) / "translations.json"
    if not translations_file.exists():
        return 0
    
    try:
        with open(translations_file, "r", encoding="utf-8") as f:
            cache = json.load(f)
        
        count = 0
        for message_text, translation_data in cache.items():
            translated_text = translation_data.get("translated_text")
            
            if dry_run:
                logger.info(f"  [DRY RUN] Would migrate translation for message: {message_text[:50]}...")
                count += 1
            else:
                try:
                    db_translations.save_translation(message_text, translated_text)
                    count += 1
                except Exception as e:
                    logger.error(f"  Failed to migrate translation: {e}")
        
        return count
    except Exception as e:
        logger.error(f"Failed to migrate translations: {e}")
        return 0


def migrate_media_metadata(dry_run: bool = False) -> int:
    """Migrate media metadata from filesystem to MySQL."""
    media_dir = Path(STATE_DIRECTORY) / "media"
    if not media_dir.exists():
        return 0
    
    count = 0
    for json_file in media_dir.glob("*.json"):
        try:
            unique_id = json_file.stem
            with open(json_file, "r", encoding="utf-8") as f:
                record = json.load(f)
            
            # Filter to only core/media-specific fields
            excluded_fields = {
                "ts", "sender_id", "sender_name", "channel_id", "channel_name",
                "media_ts", "skip_fallback", "_on_disk", "agent_telegram_id",
            }
            filtered_record = {k: v for k, v in record.items() if k not in excluded_fields}
            
            # Remove sticker-specific fields if not a sticker
            if record.get("kind") != "sticker":
                sticker_fields = {"sticker_set_name", "sticker_name", "is_emoji_set", "sticker_set_title"}
                filtered_record = {k: v for k, v in filtered_record.items() if k not in sticker_fields}
            
            filtered_record["unique_id"] = unique_id
            
            if dry_run:
                logger.info(f"  [DRY RUN] Would migrate media metadata: {unique_id}")
                count += 1
            else:
                try:
                    from db import media_metadata
                    media_metadata.save_media_metadata(filtered_record)
                    count += 1
                except Exception as e:
                    logger.error(f"  Failed to migrate media metadata {unique_id}: {e}")
        except Exception as e:
            logger.error(f"Failed to migrate media metadata from {json_file}: {e}")
    
    return count


async def get_agent_telegram_ids() -> dict[str, int]:
    """
    Get mapping of agent config names to telegram IDs by authenticating agents.
    
    This authenticates agents the same way run.sh does, extracting telegram IDs
    from Telegram after authentication.
    """
    agent_ids = {}
    
    try:
        from register_agents import register_all_agents
        from agent.registry import all_agents
        from run import authenticate_agent
        
        # Register all agents first
        logger.info("Registering agents...")
        register_all_agents()
        
        # Get list of all agents
        agents_list = list(all_agents(include_disabled=True))
        logger.info(f"Found {len(agents_list)} agents to authenticate")
        
        # Authenticate all agents concurrently
        logger.info("Authenticating agents to get telegram IDs...")
        auth_tasks = [
            asyncio.create_task(authenticate_agent(agent)) for agent in agents_list
        ]
        
        # Wait for all authentication attempts to complete
        auth_results = await asyncio.gather(*auth_tasks, return_exceptions=True)
        
        # Extract agent IDs from successfully authenticated agents
        successful = 0
        for agent, result in zip(agents_list, auth_results):
            if result is True and agent.agent_id:
                agent_ids[agent.config_name] = agent.agent_id
                logger.info(f"Authenticated {agent.name} ({agent.config_name}) -> {agent.agent_id}")
                successful += 1
            elif isinstance(result, Exception):
                logger.warning(f"Failed to authenticate {agent.name} ({agent.config_name}): {result}")
            elif not agent.agent_id:
                logger.warning(f"Agent {agent.name} ({agent.config_name}) authenticated but no agent_id set")
        
        logger.info(f"Successfully authenticated {successful}/{len(agents_list)} agents")
        
        # Disconnect all clients
        for agent in agents_list:
            if agent._client:
                try:
                    await agent._client.disconnect()
                    agent.clear_client_and_caches()
                except Exception as e:
                    logger.debug(f"Error disconnecting {agent.name}: {e}")
        
    except Exception as e:
        logger.error(f"Error during agent authentication: {e}")
        logger.exception("Full traceback:")
    
    return agent_ids


async def verify_migration() -> int:
    """Verify that filesystem data matches MySQL data."""
    logger.info("Starting verification...")
    
    # Get agent mappings
    agent_ids = await get_agent_telegram_ids()
    if not agent_ids:
        logger.warning("No agents found with telegram IDs. Cannot verify agent-specific data.")
        logger.warning("Global data (translations, media metadata) will still be verified.")
    
    errors = 0
    warnings = 0
    
    # Verify per-agent data
    state_dir = Path(STATE_DIRECTORY)
    for agent_dir in state_dir.iterdir():
        if not agent_dir.is_dir() or agent_dir.name in ("media", "media_scratch"):
            continue
        
        agent_config_name = agent_dir.name
        agent_telegram_id = agent_ids.get(agent_config_name)
        
        if not agent_telegram_id:
            logger.debug(f"Skipping verification for {agent_config_name} - no telegram ID found")
            continue
        
        logger.info(f"Verifying data for agent: {agent_config_name} (ID: {agent_telegram_id})")
        
        # Verify memories
        mem_errors, mem_warnings = verify_memories(agent_config_name, agent_telegram_id)
        errors += mem_errors
        warnings += mem_warnings
        
        # Verify intentions
        int_errors, int_warnings = verify_intentions(agent_config_name, agent_telegram_id)
        errors += int_errors
        warnings += int_warnings
        
        # Verify plans
        plan_errors, plan_warnings = verify_plans(agent_config_name, agent_telegram_id)
        errors += plan_errors
        warnings += plan_warnings
        
        # Verify summaries
        sum_errors, sum_warnings = verify_summaries(agent_config_name, agent_telegram_id)
        errors += sum_errors
        warnings += sum_warnings
        
        # Verify schedules
        sched_errors, sched_warnings = verify_schedules(agent_config_name, agent_telegram_id)
        errors += sched_errors
        warnings += sched_warnings
    
    # Verify global data
    logger.info("Verifying global data...")
    
    # Verify translations
    trans_errors, trans_warnings = verify_translations()
    errors += trans_errors
    warnings += trans_warnings
    
    # Verify media metadata
    media_errors, media_warnings = verify_media_metadata()
    errors += media_errors
    warnings += media_warnings
    
    # Summary
    logger.info(f"\nVerification complete!")
    logger.info(f"  Errors: {errors}")
    logger.info(f"  Warnings: {warnings}")
    
    if errors == 0 and warnings == 0:
        logger.info("✓ All data verified successfully!")
        return 0
    elif errors == 0:
        logger.warning("⚠ Verification completed with warnings (data may be incomplete)")
        return 0
    else:
        logger.error("✗ Verification failed - data mismatches found")
        return 1


def verify_memories(agent_config_name: str, agent_telegram_id: int) -> tuple[int, int]:
    """Verify memories match between filesystem and MySQL."""
    errors = 0
    warnings = 0
    
    memory_file = Path(STATE_DIRECTORY) / agent_config_name / "memory.json"
    if not memory_file.exists():
        return 0, 0
    
    try:
        from memory_storage import load_property_entries
        
        fs_memories_list, _ = load_property_entries(
            memory_file, "memory", default_id_prefix="memory"
        )
        fs_memories = {m.get("id"): m for m in fs_memories_list}
        
        db_memories_list = memories.load_memories(agent_telegram_id)
        db_memories = {m.get("id"): m for m in db_memories_list}
        
        # Check for missing in DB
        for mem_id, fs_mem in fs_memories.items():
            if mem_id not in db_memories:
                logger.error(f"  Memory {mem_id} exists in filesystem but not in MySQL")
                errors += 1
            else:
                # Compare content (ignore metadata differences)
                if fs_mem.get("content") != db_memories[mem_id].get("content"):
                    logger.error(f"  Memory {mem_id} content mismatch")
                    errors += 1
        
        # Check for extra in DB (warnings only - could be new data)
        for mem_id in db_memories:
            if mem_id not in fs_memories:
                logger.warning(f"  Memory {mem_id} exists in MySQL but not in filesystem (may be new data)")
                warnings += 1
        
        if errors == 0 and warnings == 0:
            logger.info(f"  ✓ Memories verified: {len(fs_memories)} records")
        
    except Exception as e:
        logger.error(f"Failed to verify memories for {agent_config_name}: {e}")
        errors += 1
    
    return errors, warnings


def verify_intentions(agent_config_name: str, agent_telegram_id: int) -> tuple[int, int]:
    """Verify intentions match between filesystem and MySQL."""
    errors = 0
    warnings = 0
    
    memory_file = Path(STATE_DIRECTORY) / agent_config_name / "memory.json"
    if not memory_file.exists():
        return 0, 0
    
    try:
        from memory_storage import load_property_entries
        
        fs_intentions_list, _ = load_property_entries(
            memory_file, "intention", default_id_prefix="intent"
        )
        fs_intentions = {i.get("id"): i for i in fs_intentions_list}
        
        db_intentions_list = intentions.load_intentions(agent_telegram_id)
        db_intentions = {i.get("id"): i for i in db_intentions_list}
        
        for int_id, fs_int in fs_intentions.items():
            if int_id not in db_intentions:
                logger.error(f"  Intention {int_id} exists in filesystem but not in MySQL")
                errors += 1
            elif fs_int.get("content") != db_intentions[int_id].get("content"):
                logger.error(f"  Intention {int_id} content mismatch")
                errors += 1
        
        for int_id in db_intentions:
            if int_id not in fs_intentions:
                logger.warning(f"  Intention {int_id} exists in MySQL but not in filesystem")
                warnings += 1
        
        if errors == 0 and warnings == 0:
            logger.info(f"  ✓ Intentions verified: {len(fs_intentions)} records")
        
    except Exception as e:
        logger.error(f"Failed to verify intentions for {agent_config_name}: {e}")
        errors += 1
    
    return errors, warnings


def verify_plans(agent_config_name: str, agent_telegram_id: int) -> tuple[int, int]:
    """Verify plans match between filesystem and MySQL."""
    errors = 0
    warnings = 0
    
    memory_dir = Path(STATE_DIRECTORY) / agent_config_name / "memory"
    if not memory_dir.exists():
        return 0, 0
    
    try:
        from memory_storage import load_property_entries
        
        fs_plans = {}
        for channel_file in memory_dir.glob("*.json"):
            try:
                channel_id = int(channel_file.stem)
            except ValueError:
                continue
            
            plans_list, _ = load_property_entries(
                channel_file, "plan", default_id_prefix="plan"
            )
            for plan in plans_list:
                plan_id = plan.get("id")
                fs_plans[(channel_id, plan_id)] = plan
        
        db_plans = {}
        for channel_file in memory_dir.glob("*.json"):
            try:
                channel_id = int(channel_file.stem)
            except ValueError:
                continue
            
            plans_list = plans.load_plans(agent_telegram_id, channel_id)
            for plan in plans_list:
                plan_id = plan.get("id")
                db_plans[(channel_id, plan_id)] = plan
        
        for key, fs_plan in fs_plans.items():
            if key not in db_plans:
                logger.error(f"  Plan {key[1]} for channel {key[0]} exists in filesystem but not in MySQL")
                errors += 1
            elif fs_plan.get("content") != db_plans[key].get("content"):
                logger.error(f"  Plan {key[1]} for channel {key[0]} content mismatch")
                errors += 1
        
        for key in db_plans:
            if key not in fs_plans:
                logger.warning(f"  Plan {key[1]} for channel {key[0]} exists in MySQL but not in filesystem")
                warnings += 1
        
        if errors == 0 and warnings == 0:
            logger.info(f"  ✓ Plans verified: {len(fs_plans)} records")
        
    except Exception as e:
        logger.error(f"Failed to verify plans for {agent_config_name}: {e}")
        errors += 1
    
    return errors, warnings


def verify_summaries(agent_config_name: str, agent_telegram_id: int) -> tuple[int, int]:
    """Verify summaries match between filesystem and MySQL."""
    errors = 0
    warnings = 0
    
    memory_dir = Path(STATE_DIRECTORY) / agent_config_name / "memory"
    if not memory_dir.exists():
        return 0, 0
    
    try:
        from memory_storage import load_property_entries
        
        fs_summaries = {}
        for channel_file in memory_dir.glob("*.json"):
            try:
                channel_id = int(channel_file.stem)
            except ValueError:
                continue
            
            summaries_list, _ = load_property_entries(
                channel_file, "summary", default_id_prefix="summary"
            )
            for summary in summaries_list:
                summary_id = summary.get("id")
                fs_summaries[(channel_id, summary_id)] = summary
        
        db_summaries = {}
        for channel_file in memory_dir.glob("*.json"):
            try:
                channel_id = int(channel_file.stem)
            except ValueError:
                continue
            
            summaries_list = summaries.load_summaries(agent_telegram_id, channel_id)
            for summary in summaries_list:
                summary_id = summary.get("id")
                db_summaries[(channel_id, summary_id)] = summary
        
        for key, fs_sum in fs_summaries.items():
            channel_id, summary_id = key
            if key not in db_summaries:
                logger.error(f"  Summary {summary_id} for channel {channel_id} exists in filesystem but not in MySQL")
                logger.debug(f"    FS summary keys: {list(fs_sum.keys())}")
                logger.debug(f"    FS content length: {len(fs_sum.get('content', ''))}")
                errors += 1
            else:
                fs_content = fs_sum.get("content", "")
                db_content = db_summaries[key].get("content", "")
                if fs_content != db_content:
                    # Show a snippet of the difference for debugging
                    fs_preview = fs_content[:200] if len(fs_content) > 200 else fs_content
                    db_preview = db_content[:200] if len(db_content) > 200 else db_content
                    logger.error(f"  Summary {summary_id} for channel {channel_id} content mismatch")
                    logger.error(f"    FS length: {len(fs_content)}, DB length: {len(db_content)}")
                    logger.error(f"    FS content (first 200 chars): {fs_preview}")
                    logger.error(f"    DB content (first 200 chars): {db_preview}")
                    # Show character-by-character difference at start
                    if len(fs_content) > 0 and len(db_content) > 0:
                        diff_pos = next((i for i, (a, b) in enumerate(zip(fs_content, db_content)) if a != b), min(len(fs_content), len(db_content)))
                        if diff_pos < 50:
                            logger.error(f"    First difference at position {diff_pos}")
                            logger.error(f"    FS char at {diff_pos}: {repr(fs_content[diff_pos:diff_pos+20])}")
                            logger.error(f"    DB char at {diff_pos}: {repr(db_content[diff_pos:diff_pos+20])}")
                    errors += 1
        
        for key in db_summaries:
            if key not in fs_summaries:
                logger.warning(f"  Summary {key[1]} for channel {key[0]} exists in MySQL but not in filesystem")
                warnings += 1
        
        if errors == 0 and warnings == 0:
            logger.info(f"  ✓ Summaries verified: {len(fs_summaries)} records")
        
    except Exception as e:
        logger.error(f"Failed to verify summaries for {agent_config_name}: {e}")
        errors += 1
    
    return errors, warnings


def verify_schedules(agent_config_name: str, agent_telegram_id: int) -> tuple[int, int]:
    """Verify schedules match between filesystem and MySQL."""
    errors = 0
    warnings = 0
    
    schedule_file = Path(STATE_DIRECTORY) / agent_config_name / "schedule.json"
    if not schedule_file.exists():
        return 0, 0
    
    try:
        with open(schedule_file, "r", encoding="utf-8") as f:
            fs_schedule = json.load(f)
        
        db_schedule = schedules.load_schedule(agent_telegram_id)
        
        if db_schedule is None:
            logger.error(f"  Schedule exists in filesystem but not in MySQL")
            logger.debug(f"    FS schedule keys: {list(fs_schedule.keys()) if isinstance(fs_schedule, dict) else 'not a dict'}")
            errors += 1
        else:
            # Compare schedules by comparing the actual data structures
            # MySQL JSON columns normalize JSON, so we compare the parsed objects
            # Normalize both by parsing and re-serializing with consistent settings
            fs_normalized = json.loads(json.dumps(fs_schedule, sort_keys=True))
            db_normalized = json.loads(json.dumps(db_schedule, sort_keys=True))
            
            # Compare the normalized objects directly
            if fs_normalized != db_normalized:
                logger.error(f"  Schedule content mismatch")
                # Serialize for display purposes
                fs_json = json.dumps(fs_normalized, sort_keys=True, separators=(',', ':'))
                db_json = json.dumps(db_normalized, sort_keys=True, separators=(',', ':'))
                logger.error(f"    FS schedule length: {len(fs_json)}, DB schedule length: {len(db_json)}")
                # Show first difference
                if len(fs_json) > 0 and len(db_json) > 0:
                    diff_pos = next((i for i, (a, b) in enumerate(zip(fs_json, db_json)) if a != b), min(len(fs_json), len(db_json)))
                    logger.error(f"    First difference at position {diff_pos}")
                    logger.error(f"    FS JSON (first 300 chars): {fs_json[:300]}")
                    logger.error(f"    DB JSON (first 300 chars): {db_json[:300]}")
                    if diff_pos < 300:
                        logger.error(f"    FS around diff: {repr(fs_json[max(0, diff_pos-50):diff_pos+50])}")
                        logger.error(f"    DB around diff: {repr(db_json[max(0, diff_pos-50):diff_pos+50])}")
                errors += 1
            else:
                logger.info(f"  ✓ Schedule verified")
        
    except Exception as e:
        logger.error(f"Failed to verify schedule for {agent_config_name}: {e}")
        errors += 1
    
    return errors, warnings


def verify_translations() -> tuple[int, int]:
    """Verify translations match between filesystem and MySQL."""
    errors = 0
    warnings = 0
    
    translations_file = Path(STATE_DIRECTORY) / "translations.json"
    if not translations_file.exists():
        return 0, 0
    
    try:
        with open(translations_file, "r", encoding="utf-8") as f:
            fs_cache = json.load(f)
        
        fs_count = len(fs_cache)
        db_count = 0
        missing = 0
        
        for message_text, translation_data in fs_cache.items():
            translated_text = translation_data.get("translated_text")
            db_translation = db_translations.get_translation(message_text)
            
            if db_translation is None:
                missing += 1
                logger.warning(f"  Translation for message hash not found in MySQL")
                warnings += 1
            elif db_translation != translated_text:
                logger.error(f"  Translation content mismatch for message hash")
                errors += 1
            else:
                db_count += 1
        
        if errors == 0:
            logger.info(f"  ✓ Translations verified: {db_count}/{fs_count} records")
            if missing > 0:
                logger.warning(f"    ({missing} translations not found in MySQL - may be due to hash differences)")
        
    except Exception as e:
        logger.error(f"Failed to verify translations: {e}")
        errors += 1
    
    return errors, warnings


def verify_media_metadata() -> tuple[int, int]:
    """Verify media metadata matches between filesystem and MySQL."""
    errors = 0
    warnings = 0
    
    media_dir = Path(STATE_DIRECTORY) / "media"
    if not media_dir.exists():
        return 0, 0
    
    try:
        fs_records = {}
        for json_file in media_dir.glob("*.json"):
            unique_id = json_file.stem
            with open(json_file, "r", encoding="utf-8") as f:
                record = json.load(f)
            fs_records[unique_id] = record
        
        missing = 0
        for unique_id, fs_record in fs_records.items():
            db_record = media_metadata.load_media_metadata(unique_id)
            if db_record is None:
                missing += 1
                logger.warning(f"  Media metadata {unique_id} exists in filesystem but not in MySQL")
                warnings += 1
        
        if missing == 0:
            logger.info(f"  ✓ Media metadata verified: {len(fs_records)} records")
        else:
            logger.warning(f"  Media metadata: {len(fs_records) - missing}/{len(fs_records)} records found")
        
    except Exception as e:
        logger.error(f"Failed to verify media metadata: {e}")
        errors += 1
    
    return errors, warnings


async def main_async():
    """Main migration function (async)."""
    parser = argparse.ArgumentParser(description="Migrate data from filesystem to MySQL")
    parser.add_argument("--dry-run", action="store_true", help="Validate without making changes")
    parser.add_argument("--resume", action="store_true", help="Resume interrupted migration")
    parser.add_argument("--verify", action="store_true", help="Verify migration")
    parser.add_argument("--verbose", "-v", action="store_true", help="Enable verbose logging (DEBUG level)")
    args = parser.parse_args()
    
    # Set logging level to DEBUG if verbose
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)
        logger.debug("Verbose logging enabled")
    
    if args.verify:
        logger.info("Verification mode - checking data integrity...")
        return await verify_migration()
    
    # Create schema if needed
    if not args.dry_run:
        logger.info("Creating database schema...")
        try:
            create_schema()
            logger.info("Schema created successfully")
        except Exception as e:
            logger.error(f"Failed to create schema: {e}")
            return 1
    
    # Get agent mappings by authenticating agents
    agent_ids = await get_agent_telegram_ids()
    if not agent_ids:
        logger.warning("No agents found with telegram IDs. Some data may not be migratable.")
        logger.warning("")
        logger.warning("Make sure agents are authenticated (session files exist and are valid).")
        logger.warning("Global data (translations, media metadata) will still be migrated.")
    
    total_migrated = 0
    
    # Migrate per-agent data
    state_dir = Path(STATE_DIRECTORY)
    for agent_dir in state_dir.iterdir():
        if not agent_dir.is_dir() or agent_dir.name in ("media", "media_scratch"):
            continue
        
        agent_config_name = agent_dir.name
        agent_telegram_id = agent_ids.get(agent_config_name)
        
        if not agent_telegram_id:
            logger.warning(f"Skipping {agent_config_name} - no telegram ID found")
            continue
        
        logger.info(f"Migrating data for agent: {agent_config_name} (ID: {agent_telegram_id})")
        
        # Migrate memories
        logger.info(f"  Migrating memories...")
        count = migrate_memories(agent_config_name, agent_telegram_id, args.dry_run)
        logger.info(f"  Migrated {count} memories")
        total_migrated += count
        
        # Migrate intentions
        logger.info(f"  Migrating intentions...")
        count = migrate_intentions(agent_config_name, agent_telegram_id, args.dry_run)
        logger.info(f"  Migrated {count} intentions")
        total_migrated += count
        
        # Migrate plans
        logger.info(f"  Migrating plans...")
        count = migrate_plans(agent_config_name, agent_telegram_id, args.dry_run)
        logger.info(f"  Migrated {count} plans")
        total_migrated += count
        
        # Migrate summaries
        logger.info(f"  Migrating summaries...")
        count = migrate_summaries(agent_config_name, agent_telegram_id, args.dry_run, args.verbose)
        logger.info(f"  Migrated {count} summaries")
        total_migrated += count
        
        # Migrate schedules
        logger.info(f"  Migrating schedules...")
        count = migrate_schedules(agent_config_name, agent_telegram_id, args.dry_run)
        logger.info(f"  Migrated {count} schedules")
        total_migrated += count
    
    # Migrate global data
    logger.info("Migrating global data...")
    
    # Migrate translations
    logger.info("  Migrating translations...")
    count = migrate_translations(args.dry_run)
    logger.info(f"  Migrated {count} translations")
    total_migrated += count
    
    # Migrate media metadata
    logger.info("  Migrating media metadata...")
    count = migrate_media_metadata(args.dry_run)
    logger.info(f"  Migrated {count} media metadata records")
    total_migrated += count
    
    logger.info(f"\nMigration complete! Total records migrated: {total_migrated}")
    if args.dry_run:
        logger.info("This was a dry run - no changes were made")
    
    return 0


def main():
    """Synchronous wrapper for async main function."""
    return asyncio.run(main_async())


if __name__ == "__main__":
    sys.exit(main())

