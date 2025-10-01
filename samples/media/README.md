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

The system checks these directories in order of precedence (all in config directories, NOT state):

For each config directory in `CINDY_AGENT_CONFIG_PATH`:
1. **Agent-specific curated**: `{config_dir}/agents/{AgentName}/media/` (if exists)
2. **Global curated**: `{config_dir}/media/` (if exists)

Then:
3. **AI cache** (state, not config): `state/media/` (cached AI-generated descriptions)
4. **Budget management**: Returns fallback if budget exhausted
5. **AI generation**: Always succeeds (generates new description or returns fallback)

**Important**: Curated descriptions are configuration data and should NEVER be placed in the `state/` directory.
The `state/` directory is only for runtime state like AI-generated cache files.

## Full directory structure example

Here's a complete example showing how to organize curated media descriptions:

```
samples/                                    # Config directory (or your custom config dir)
├── agents/
│   ├── Wendy.md                           # Agent configuration file
│   ├── Wendy/
│   │   └── media/                         # Wendy-specific curated media
│   │       ├── 123456789.json            # Curated description for this sticker
│   │       └── 987654321.json
│   ├── Heidi.md
│   └── Heidi/
│       └── media/                         # Heidi-specific curated media
│           └── 444555666.json
└── media/                                  # Global curated media (all agents)
    ├── 901422453274706125.json            # Example: MrRibbit 💻
    └── 901422453274706125.webp

state/                                      # State directory (runtime only)
└── media/                                  # AI-generated cache (created automatically)
    ├── 777888999.json                     # AI-generated description
    └── 777888999.webp
```

### Directory purposes

- **Global curated** (`samples/media/`): Descriptions shared by all agents
- **Agent curated** (`samples/agents/Wendy/media/`): Descriptions specific to Wendy
- **AI cache** (`state/media/`): AI-generated descriptions (not version-controlled)

### Precedence example

If agent Wendy receives a sticker with unique_id `123456789`:

1. Check `samples/agents/Wendy/media/123456789.json` (highest priority)
2. Check `samples/media/123456789.json`
3. Check `state/media/123456789.json` (AI cache)
4. If not found and budget available: Generate with AI and cache to `state/media/123456789.json`
5. If budget exhausted: Return fallback without description

## Usage

1. Find the unique ID of the media item you want to curate (check the logs or existing cache files in `state/media/`)
2. Create a JSON file named `<unique_id>.json` in this directory
3. Add the curated description following the format above
4. The system will automatically use your curated description instead of generating one with AI

## Future enhancements

- Side-by-side editing tool for descriptions and media
- Web UI for managing curated descriptions
