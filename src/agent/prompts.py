# agent/prompts.py

# Copyright (c) 2025 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.

"""
System prompt building for Agent.
"""

import logging
from typing import TYPE_CHECKING

from prompt_loader import load_system_prompt
from core.prompt_utils import substitute_templates

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from agent import Agent


class AgentPromptMixin:
    """Mixin providing system prompt building capabilities."""

    def _build_system_prompt(self, channel_name, specific_instructions, channel_id: int | None = None, for_summarization: bool = False):
        """
        Private helper to build the system prompt.
        
        Args:
            channel_name: The human/user display name used for template substitution.
            specific_instructions: Paragraph injected into the LLM prompt.
            channel_id: Optional channel ID for loading channel-specific plans.
            for_summarization: If True, use Instructions-Summarize.md and filter Task-* prompts.
        
        Returns:
            Base system prompt string
        """
        prompt_parts = []

        # Add specific instructions for the current turn
        if specific_instructions:
            prompt_parts.append(specific_instructions)

        # Add LLM-specific prompt
        if for_summarization:
            llm_prompt = load_system_prompt("Instructions-Summarize")
        else:
            # Build intentions section with plans (if any) before intentions
            intention_parts = []
            
            # Load channel plans first (before intentions)
            if channel_id is not None:
                plan_content = self._load_plan_content(channel_id)
                if plan_content:
                    intention_parts.append("# Channel Plan\n\n```json\n" + plan_content + "\n```")
            
            # Load intentions
            intention_content = self._load_intention_content()
            if intention_content:
                intention_parts.append("# Intentions\n\n```json\n" + intention_content + "\n```")
            
            # Add intentions section if we have any content
            if intention_parts:
                prompt_parts.append("\n\n".join(intention_parts))

            llm_prompt = load_system_prompt(self.llm.prompt_name)
        prompt_parts.append(llm_prompt)

        # Add agent instructions
        instructions = (self.instructions or "").strip()
        if instructions:
            prompt_parts.append(f"# Agent Instructions\n\n{instructions}")

        # Add role prompts
        if for_summarization:
            # Exclude Task-* prompts except Task-Summarize
            for role_prompt_name in self.role_prompt_names:
                # Skip Task-* prompts except Task-Summarize
                if role_prompt_name.startswith("Task-"):
                    continue
                role_prompt = load_system_prompt(role_prompt_name)
                prompt_parts.append(role_prompt)
            
            # Always include Task-Summarize.md
            summarize_prompt = load_system_prompt("Task-Summarize")
            prompt_parts.append(summarize_prompt)
        else:
            # Add all role prompts in order, but exclude Task-Schedule
            # Task-Schedule.md is added conditionally based on context (see build_complete_system_prompt)
            for role_prompt_name in self.role_prompt_names:
                # Skip Task-Schedule - it's added conditionally when schedule.json is in context
                if role_prompt_name == "Task-Schedule":
                    continue
                role_prompt = load_system_prompt(role_prompt_name)
                prompt_parts.append(role_prompt)

        # Apply template substitution across the assembled prompt
        final_prompt = "\n\n".join(prompt_parts)
        final_prompt = substitute_templates(final_prompt, self.name, channel_name)
        return final_prompt

    def get_system_prompt(self, channel_name, specific_instructions, channel_id: int | None = None):
        """
        Get the base system prompt for this agent (core prompt components only).

        This includes:
        1. Specific instructions for the current turn
        2. Channel Plan (if channel_id provided)
        3. Intentions (if any)
        4. Instructions prompt (Instructions.md) - shared across all LLMs
        5. All role prompts (in order)
        6. Agent instructions

        Note: Memory content is added later in the prompt construction process,
        positioned after stickers and before current time.

        Args:
            channel_name: The human/user display name used for template substitution.
            specific_instructions: Paragraph injected into the LLM prompt before .
            channel_id: Optional channel ID for loading channel-specific plans.

        Returns:
            Base system prompt string
        """
        return self._build_system_prompt(channel_name, specific_instructions, channel_id=channel_id, for_summarization=False)

    def get_system_prompt_for_summarization(self, channel_name, specific_instructions):
        """
        Get the base system prompt for summarization tasks.
        
        This is similar to get_system_prompt but:
        - Uses Instructions-Summarize.md instead of Instructions.md
        - Excludes Task-*.md prompts from role prompts
        - Includes Task-Summarize.md
        
        Args:
            channel_name: The human/user display name used for template substitution.
            specific_instructions: Paragraph injected into the LLM prompt.
        
        Returns:
            Base system prompt string for summarization
        """
        return self._build_system_prompt(channel_name, specific_instructions, for_summarization=True)
