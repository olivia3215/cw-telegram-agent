# Telepathic Channels

This file configures which channels, groups, or users are "telepathic" - meaning the agent's thoughts, memories, and retrievals are visible to conversation participants.

Each line starting with `- ` followed by a number designates a Telegram channel, group, or user ID.

## Examples

- 123456789
- -987654321
- 555666777

## How it works

When an agent is in a telepathic channel:
- **Think tasks** are sent as `⟦think⟧` messages visible to all participants
- **Remember tasks** are sent as `⟦remember⟧` messages visible to all participants  
- **Retrieve tasks** are sent as `⟦retrieve⟧` messages visible to all participants
- The agent cannot see its own telepathic messages, maintaining the illusion of telepathy

## Configuration

Place this file (`Telepaths.md`) in any configuration directory specified by `CINDY_AGENT_CONFIG_PATH`.
Multiple configuration directories are supported - telepathic channels from all directories are combined.
