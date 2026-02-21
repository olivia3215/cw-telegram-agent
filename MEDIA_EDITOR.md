# Media Editor Documentation

The Media Editor is a web-based application that provides a user-friendly interface for managing media descriptions used by your Telegram agents. It allows you to browse, edit, import, and organize media descriptions across all your agents and directories.

## Table of Contents

- [Overview](#overview)
- [Getting Started](#getting-started)
- [Web Interface](#web-interface)
- [Browsing and Filtering Media](#browsing-and-filtering-media)
- [Managing Media](#managing-media)
- [Importing Sticker Sets](#importing-sticker-sets)
- [AI Integration](#ai-integration)
- [Directory Management](#directory-management)
- [API Reference](#api-reference)
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
   ./telegram_login.sh
   ```

### Starting the Media Editor

The admin console (which includes the media editor) is automatically started with the agent server.

1. **Start the agent server**:
   ```bash
   ./run.sh start
   ```
   The admin console will be available on port 5001 by default (configurable via `CINDY_ADMIN_CONSOLE_PORT`).

2. **Access the web interface**:
   Open your browser and navigate to: http://localhost:5001

3. **Check status** (optional):
   ```bash
   ./run.sh status
   ```

4. **View logs** (if needed):
   ```bash
   ./run.sh logs
   ```

5. **Stop the server** (when done):
   ```bash
   ./run.sh stop
   ```

## Web Interface

### Main Layout

The Media Editor interface consists of:

- **Directory Selector**: Dropdown to choose which media directory to view
- **Limit Field** (state/media only): Constrains the view to N most recent items
- **Search Box**: Search across media ID, sticker sets, sticker names, and descriptions
- **Media Type Filter**: Filter by stickers, emoji sets, videos, photos, audio, or other
- **Pagination Controls**: Navigate through pages of results
- **Media Grid**: Visual display of media items on the current page
- **Media Items**: Each item shows:
  - **Preview**: Image, video, or sticker preview
  - **Description**: Editable text area with auto-save
  - **Status**: Shows "Saved", "Saving...", "Error", or other states
  - **Actions**: Buttons for AI refresh, move, and delete operations

### Performance Optimization

For large media collections, the Media Editor uses backend pagination:

- **Fast loading**: Page loads in < 1 second regardless of dataset size
- **Backend filtering**: Search and media type filters are applied at the database/filesystem level
- **Smart pagination**: Only loads items for the current page
- **Limit control**: Constrain the working set to recent items for faster browsing

### Directory Types

The Media Editor can display several types of directories:

1. **Global Directories**: `{config_dir}/media/`
   - Shared media descriptions across all agents
   - Contains curated descriptions for all agents

2. **AI Cache**: `state/media/`
   - Contains AI-generated descriptions and cached results
   - Used by the media pipeline for performance

### Auto-Save Functionality

The Media Editor features intelligent auto-save:

- **1-second delay**: Changes are saved automatically after 1 second of inactivity
- **Debounced**: New edits extend the delay, preventing excessive saves
- **Visual feedback**: Status indicators show "Typing...", "Saving...", "Saved", or "Error"
- **Status tracking**: Manually edited descriptions are marked as "curated"

## Browsing and Filtering Media

The Media Editor provides powerful tools for finding and browsing media in large collections.

### Pagination

When viewing directories with many media items, the interface automatically paginates results:

- **Default page size**: 10 items per page (configurable)
- **Page navigation**: Use Previous/Next buttons or page selector dropdown
- **Page indicator**: Shows current page, total pages, and total items
- **Fast loading**: Each page loads independently for optimal performance

### Search Functionality

Search for media across multiple fields:

1. **Enter search query** in the search box
2. **Automatic search**: Results appear after 300ms of inactivity (debounced)
3. **Clear button**: Click to clear search and return to full list

**Search fields:**
- Media ID (unique_id)
- Sticker set name
- Sticker name
- Description text

**Search features:**
- Case-insensitive substring matching
- Searches across all fields simultaneously
- Results are paginated
- Clear visual indicator when search is active

### Media Type Filtering

Filter media by category using the "Filter by type" dropdown:

- **All Media**: No filtering (default)
- **Stickers**: Regular sticker sets (excludes emoji sets)
- **Emoji Sets**: Custom emoji sticker sets only
- **Videos**: Video and animation media
- **Photos**: Photo/image media
- **Audio**: Audio media files
- **Other**: Media not in the above categories

**Filter features:**
- Combines with search (both filters applied)
- Maintains pagination
- Shows active filter in pagination info

### Limit Control (state/media only)

For the `state/media` directory with large datasets:

1. **Enter a number** in the "Limit to N most recent items" field
2. **Wait 500ms** - results update automatically
3. **View constrained to N most recent** items by modification time

**Use cases:**
- Focus on recently added media
- Improve performance with huge datasets
- Combine with search/filter for targeted browsing

### Combined Filtering

All filters work together:

**Example**: View only sticker sets containing "dancing" within the 1,000 most recent items:
1. Set limit to 1000
2. Select "Stickers" from media type filter
3. Enter "dancing" in search box
4. Navigate through paginated results

**Processing order:**
1. Limit (if specified) - constrain to N most recent
2. Media type filter - apply category filter
3. Search - apply text search
4. Pagination - display current page

### Pagination Info Display

The pagination area shows:
- Current page and total pages
- Total items matching filters
- Active search query (if any)
- Active media type filter (if any)

**Example**: "Page 2 of 5 (47 items) • Search: 'cat' • Type: Stickers"

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
   - Video stickers (.webm): Receive video-level AI analysis and display with a video player
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
- **Video stickers (.webm)**: Receive video-level AI analysis and display with a video player
- **TGS files**: Marked as "unsupported_format" without AI processing
- **Video duration limit**: Videos longer than the configured maximum (default 10 seconds) are marked unsupported and are not sent for AI description. Set `MEDIA_VIDEO_MAX_DURATION_SECONDS` in your environment (or `.env`) to allow longer videos; see [Configuration](#configuration) below.
- **Cached results**: Stored in `state/media` for performance
- **Status tracking**: Distinguishes between AI-generated and curated content

### Configuration

- **`MEDIA_VIDEO_MAX_DURATION_SECONDS`** (default: `10`): Maximum video duration in seconds for AI description. Videos longer than this are marked "unsupported" and are not analyzed. Increase this to allow longer videos (e.g. `20` or `30`); the value is read from the environment when the process starts.

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

1. **Global** (`{config_dir}/media/`) - highest priority
2. **AI cache** (`state/media/`) - lowest priority (fallback)

### Auto-Creation

- **Target directories**: Created when moving media items
- **Media cache**: Created automatically by the AI pipeline

## API Reference

The Media Editor exposes a REST API for programmatic access. For detailed API documentation including query parameters, response formats, and examples, see the [Media Editor API section in DESIGN.md](DESIGN.md#media-editor-api).

### Quick Reference

**Endpoint:** `GET /admin/api/media`

**Key Parameters:**
- `directory` (required): Media directory path
- `page`: Page number (default: 1)
- `page_size`: Items per page (default: 10, max: 100)
- `search`: Search query across ID, sticker set, sticker name, description
- `media_type`: Filter by type (`all`, `stickers`, `emoji`, `video`, `photos`, `audio`, `other`)
- `limit`: Constrain to N most recent items

**Example Request:**
```http
GET /admin/api/media?directory=state/media&page=2&page_size=20&media_type=stickers&search=cat
```

**Response includes:**
- `media_files`: Array of media items for current page
- `grouped_media`: Items grouped by sticker set
- `pagination`: Metadata (page, total_pages, total_items, active filters)

For complete API documentation, security considerations, and performance details, see [DESIGN.md](DESIGN.md#media-editor-api).

## Troubleshooting

### Common Issues

**Media Editor won't start**
- Check that all environment variables are set
- Ensure dependencies are installed: `pip install -r requirements.txt`
- Verify port 5001 is available: `./run.sh status`

**Sticker import fails**
- Ensure Telegram sessions are logged in: `./telegram_login.sh`
- Check that the sticker set name is correct and public
- Verify API credentials are valid

**AI descriptions not generating**
- Check that `GOOGLE_GEMINI_API_KEY` is set and valid
- Ensure budget is available (restart the media editor to reset)
- Check logs for specific error messages: `./run.sh logs`

**Changes not saving**
- Check browser console for JavaScript errors
- Verify the media editor server is running
- Ensure you have write permissions to the target directories

**Media not displaying**
- Check that media files exist in the expected locations
- Verify MIME types are correctly detected
- Ensure the media editor has read access to the files

**Stickers missing sticker set name**
- Cached stickers that lack sticker set metadata will be auto-resolved when viewed in the Media Editor or conversation view; the system fetches the sticker set from Telegram and updates the metadata

### Debugging

**Enable debug logging**:
```bash
export GEMINI_DEBUG_LOGGING=true
./run.sh restart
```

**View detailed logs**:
```bash
./run.sh logs | tail -50
```

**Check server status**:
```bash
./run.sh status
```

**Reset budget** (if AI descriptions aren't generating):
```bash
./run.sh restart
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
