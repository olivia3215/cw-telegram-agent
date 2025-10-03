# Media Editor Documentation

The Media Editor is a web-based application that provides a user-friendly interface for managing media descriptions used by your Telegram agents. It allows you to browse, edit, import, and organize media descriptions across all your agents and directories.

## Table of Contents

- [Overview](#overview)
- [Getting Started](#getting-started)
- [Web Interface](#web-interface)
- [Managing Media](#managing-media)
- [Importing Sticker Sets](#importing-sticker-sets)
- [AI Integration](#ai-integration)
- [Directory Management](#directory-management)
- [Troubleshooting](#troubleshooting)

## Overview

The Media Editor serves as a central hub for managing the media descriptions that your Telegram agents use to understand and respond to images, stickers, and other media content. It provides:

- **Visual browsing** of all media across agents and directories
- **Real-time editing** with auto-save functionality
- **AI-powered description generation** using the same pipeline as your agents
- **Sticker set import** directly from Telegram
- **Media organization** with move and delete capabilities
- **Status tracking** to distinguish between AI-generated and curated descriptions

## Getting Started

### Prerequisites

Before using the Media Editor, ensure you have:

1. **Environment variables set up** (same as for the main agent):
   ```bash
   export CINDY_AGENT_STATE_DIR="$(pwd)/state"
   export CINDY_AGENT_CONFIG_PATH="$(pwd)/samples"
   export GOOGLE_GEMINI_API_KEY="your_api_key_here"
   export TELEGRAM_API_ID="your_api_id_here"
   export TELEGRAM_API_HASH="your_api_hash_here"
   ```

2. **Dependencies installed**:
   ```bash
   pip install -r requirements.txt
   ```

3. **Telegram sessions logged in** (for sticker import):
   ```bash
   PYTHONPATH=src python telegram_login.py
   ```

### Starting the Media Editor

1. **Start the server**:
   ```bash
   ./media_editor.sh start
   ```

2. **Access the web interface**:
   Open your browser and navigate to: http://localhost:5001

3. **Check status** (optional):
   ```bash
   ./media_editor.sh status
   ```

4. **View logs** (if needed):
   ```bash
   ./media_editor.sh logs
   ```

5. **Stop the server** (when done):
   ```bash
   ./media_editor.sh stop
   ```

## Web Interface

### Main Layout

The Media Editor interface consists of:

- **Directory Selector**: Dropdown to choose which media directory to view
- **Media Grid**: Visual display of all media items in the selected directory
- **Media Items**: Each item shows:
  - **Preview**: Image, video, or sticker preview
  - **Description**: Editable text area with auto-save
  - **Status**: Shows "Saved", "Saving...", "Error", or other states
  - **Actions**: Buttons for AI refresh, move, and delete operations

### Directory Types

The Media Editor can display several types of directories:

1. **Agent Directories**: `{config_dir}/agents/{AgentName}/media/`
   - Contains media specific to a particular agent
   - Auto-created when first accessed

2. **Global Directories**: `{config_dir}/media/`
   - Shared media descriptions across all agents
   - Higher priority than agent-specific descriptions

3. **AI Cache**: `state/media/`
   - Contains AI-generated descriptions and cached results
   - Used by the media pipeline for performance

### Auto-Save Functionality

The Media Editor features intelligent auto-save:

- **1-second delay**: Changes are saved automatically after 1 second of inactivity
- **Debounced**: New edits extend the delay, preventing excessive saves
- **Visual feedback**: Status indicators show "Typing...", "Saving...", "Saved", or "Error"
- **Status tracking**: Manually edited descriptions are marked as "curated"

## Managing Media

### Editing Descriptions

1. **Click on any description text area** to start editing
2. **Type your changes** - the status will show "Typing..."
3. **Wait 1 second** after stopping - the status will show "Saving..."
4. **Status changes to "Saved"** when the edit is complete
5. **Status changes to "curated"** to indicate manual editing

### Moving Media Items

1. **Select a media item** you want to move
2. **Choose destination** from the "Move to" dropdown
3. **Click "Move"** button
4. **Confirm the action** in the popup dialog
5. **Item disappears** from current view if moved to different directory

### Deleting Media Items

1. **Select a media item** you want to delete
2. **Click "Delete"** button
3. **Confirm deletion** in the popup dialog
4. **Item is permanently removed** from both the directory and the interface

### Refreshing from AI

1. **Click "Refresh from AI"** button next to any media item
2. **Button shows "Generating..."** during AI processing
3. **New description appears** when AI generation completes
4. **Status updates** to reflect the new AI-generated content

## Importing Sticker Sets

The Media Editor can import entire sticker sets from Telegram:

### Basic Import

1. **Navigate to the directory** where you want to import stickers
2. **Enter the sticker set name** in the import form (e.g., "OliviaAI")
3. **Click "Import Sticker Set"**
4. **Wait for processing** - the system will:
   - Download all stickers from the set
   - Generate AI descriptions for each sticker
   - Save both the media files and JSON descriptions
   - Cache results in `state/media` for performance

### Import Process Details

The import process:

1. **Authenticates** using existing Telegram sessions
2. **Downloads media** for each sticker in the set
3. **Detects MIME types** automatically
4. **Generates descriptions** using the AI pipeline
5. **Handles special cases**:
   - AnimatedEmojies: Uses emoji names as descriptions
   - Unsupported formats (TGS): Marks as "unsupported_format"
   - Budget management: Allows 10 AI descriptions per import session

### Import Tips

- **Use agent-specific directories** for stickers that should be specific to one agent
- **Use global directories** for stickers that should be available to all agents
- **Check the AI cache** (`state/media`) to see all generated descriptions
- **Edit descriptions** after import to customize them for your needs

## AI Integration

The Media Editor uses the same AI infrastructure as your Telegram agents:

### Media Pipeline

The AI integration follows this pipeline:

1. **UnsupportedFormatMediaSource**: Checks if media format is supported
2. **DirectoryMediaSource**: Looks for existing descriptions in directories
3. **AIGeneratingMediaSource**: Generates new descriptions using Gemini
4. **BudgetExhaustedMediaSource**: Manages AI usage limits

### Budget Management

- **Import sessions**: 10 AI descriptions per sticker set import
- **Refresh operations**: Uses available budget for individual refreshes
- **Cache hits**: Don't consume budget (existing descriptions are reused)
- **Unsupported formats**: Don't consume budget (TGS files, etc.)

### Special Handling

- **AnimatedEmojies**: Uses emoji names instead of AI descriptions
- **TGS files**: Marked as "unsupported_format" without AI processing
- **Cached results**: Stored in `state/media` for performance
- **Status tracking**: Distinguishes between AI-generated and curated content

## Directory Management

### Directory Structure

The Media Editor works with this directory structure:

```
{config_dir}/
├── agents/
│   └── {AgentName}/
│       └── media/          # Agent-specific media
├── media/                  # Global media (shared)
└── prompts/                # System prompts

state/
└── media/                  # AI cache and generated descriptions
```

### Directory Priority

When multiple directories contain the same media item:

1. **Agent-specific** (`agents/{AgentName}/media/`) - highest priority
2. **Global** (`{config_dir}/media/`) - medium priority
3. **AI cache** (`state/media/`) - lowest priority (fallback)

### Auto-Creation

- **Agent directories**: Created automatically when first accessed
- **Target directories**: Created when moving media items
- **Media cache**: Created automatically by the AI pipeline

## Troubleshooting

### Common Issues

**Media Editor won't start**
- Check that all environment variables are set
- Ensure dependencies are installed: `pip install -r requirements.txt`
- Verify port 5001 is available: `./media_editor.sh status`

**Sticker import fails**
- Ensure Telegram sessions are logged in: `PYTHONPATH=src python telegram_login.py`
- Check that the sticker set name is correct and public
- Verify API credentials are valid

**AI descriptions not generating**
- Check that `GOOGLE_GEMINI_API_KEY` is set and valid
- Ensure budget is available (restart the media editor to reset)
- Check logs for specific error messages: `./media_editor.sh logs`

**Changes not saving**
- Check browser console for JavaScript errors
- Verify the media editor server is running
- Ensure you have write permissions to the target directories

**Media not displaying**
- Check that media files exist in the expected locations
- Verify MIME types are correctly detected
- Ensure the media editor has read access to the files

### Debugging

**Enable debug logging**:
```bash
export GEMINI_DEBUG_LOGGING=true
./media_editor.sh restart
```

**View detailed logs**:
```bash
./media_editor.sh logs | tail -50
```

**Check server status**:
```bash
./media_editor.sh status
```

**Reset budget** (if AI descriptions aren't generating):
```bash
./media_editor.sh restart
```

### Performance Tips

- **Use the AI cache**: Generated descriptions are cached in `state/media`
- **Edit descriptions**: Manually curated descriptions don't consume AI budget
- **Batch operations**: Import multiple sticker sets in sequence
- **Monitor budget**: Check logs to see budget consumption patterns

## Advanced Usage

### Custom Media Sources

The Media Editor integrates with the existing media pipeline, so you can:

- **Add custom media sources** by extending the `MediaSource` class
- **Modify AI behavior** by adjusting the media pipeline configuration
- **Add new media types** by updating MIME type detection

### Integration with Main Agent

The Media Editor shares:

- **Same AI infrastructure** as the main Telegram agent
- **Same caching system** for performance
- **Same authentication** for Telegram API access
- **Same configuration** for agents and prompts

This ensures consistency between the media editor and your running agents.

---

For more technical details about the media pipeline and AI integration, see [DESIGN.md](DESIGN.md) and [DEVELOPER.md](DEVELOPER.md).
