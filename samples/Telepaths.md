# Telepathic Channels

This file configures which channels, groups, or users are "telepathic" - meaning a agent's thoughts, memories, and retrievals are visible to conversation participants.

Each line starting with `- ` followed by a number designates a Telegram channel, group, or user ID.

## Examples

Users generally seem to have positive IDs
- 6754281260

Groups generally seem to have negative IDs
// - -1002100080800

## How it works

When an agent is in a telepathic channel:

* **Think tasks** are sent as `⟦think⟧` messages visible to all participants
* **Remember tasks** are sent as `⟦remember⟧` messages visible to all participants  
* **Retrieve tasks** are sent as `⟦retrieve⟧` messages visible to all participants
* The agent cannot see its own telepathic messages, maintaining the illusion of telepathy

## Configuration

Place this file (`Telepaths.md`) in any configuration directory specified by `CINDY_AGENT_CONFIG_PATH`.
Multiple configuration directories are supported - telepathic channels from all directories are combined.
