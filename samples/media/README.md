# Curated Media Descriptions

This directory contains curated descriptions for media items (images, stickers, etc.) that override AI-generated descriptions.

## How it works

The system checks for curated descriptions before falling back to AI-generated descriptions. Curated descriptions take precedence and are flagged with `"status": "curated"` in the cache.

## File format

Each curated description is stored as a JSON file named `<unique_id>.json` where `<unique_id>` is the Telegram file unique ID.

### Example file: `example_curated_description.json`

```json
{
  "description": "A friendly cartoon cat waving hello with a big smile",
  "kind": "sticker",
  "sticker_set_name": "ExampleSet",
  "sticker_name": "😸",
  "curated_by": "example_user"
}
```

### Required fields

- `description`: The curated description text (required)

### Optional fields

- `kind`: Media type (`"sticker"`, `"photo"`, `"gif"`, `"animation"`)
- `sticker_set_name`: Name of the sticker set (for stickers)
- `sticker_name`: Name/emoji of the sticker (for stickers)
- `curated_by`: Who curated this description (for tracking)

## Configuration

The curated descriptions directory is determined by the configuration system:

- Default: `samples/media/` (this directory)
- Custom: Set `CINDY_AGENT_CONFIG_PATH` environment variable to point to your config directory, then create a `media/` subdirectory there
- Multiple directories: The system will check all config directories in order

## Directory hierarchy

The system checks these directories in order of precedence:

1. Conversation-specific: `state/{agent_id}/conversations/{user_id}/media/` (if exists)
2. Agent-specific: `state/{agent_id}/media/` (if exists)
3. Config directories: All directories in `CINDY_AGENT_CONFIG_PATH` with `media/` subdirectories
4. AI cache: `state/media/` (cached AI-generated descriptions)
5. Budget management: Returns fallback if budget exhausted
6. AI generation: Always succeeds (generates new description or returns fallback)

## Usage

1. Find the unique ID of the media item you want to curate (check the logs or existing cache files in `state/media/`)
2. Create a JSON file named `<unique_id>.json` in this directory
3. Add the curated description following the format above
4. The system will automatically use your curated description instead of generating one with AI

## Future enhancements

- Side-by-side editing tool for descriptions and media
- Web UI for managing curated descriptions
