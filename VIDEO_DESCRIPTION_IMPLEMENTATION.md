# Video Description Implementation

## Summary

This implementation adds AI-generated video descriptions to the cw-telegram-agent, addressing issue #82. The system can now describe short videos and animated stickers using Gemini's video analysis capabilities.

## Key Features

1. **Video Description Support**: Videos up to 10 seconds can be analyzed and described by AI
2. **Duration Enforcement**: Videos longer than 10 seconds are marked as "too long to analyze"
3. **Animated Sticker Support**: Animated stickers (including TGS files) are treated as short videos and can be described
4. **Multiple Video Formats**: Supports common video formats (MP4, WebM, MOV, AVI, etc.)

## Changes Made

### 1. Media Types (`src/media/media_types.py`)
- Added `duration` field to `MediaItem` dataclass to track video/animation duration

### 2. Telegram Media Extraction (`src/telegram_media.py`)
- Updated `_maybe_add_gif_or_animation()` to extract duration from:
  - Bot API animation objects
  - Telethon `DocumentAttributeVideo` attributes
- Duration is now captured for videos and animated stickers

### 3. Gemini LLM (`src/llm/gemini.py`)
- Added `VIDEO_DESCRIPTION_PROMPT` for video analysis
- Updated `is_mime_type_supported_by_llm()` to include video MIME types:
  - `video/mp4`, `video/webm`, `video/mov`, `video/mpeg`, etc.
- Added `describe_video()` method:
  - Uses Gemini 2.0 Flash model for video analysis
  - Validates video duration (rejects videos >10 seconds)
  - Returns detailed video descriptions
  - 60-second timeout (longer than images due to video processing)

### 4. Media Source Pipeline (`src/media/media_source.py`)

#### `UnsupportedFormatMediaSource`
- Added duration check for videos and animated stickers
- Rejects videos longer than 10 seconds with clear error message
- Prevents budget consumption for too-long videos

#### `AIGeneratingMediaSource`
- Updated to call `describe_video()` for video and animated_sticker kinds
- Continues to use `describe_image()` for photos and static images
- Proper error handling for `ValueError` (duration/format issues)

### 5. Media Injector (`src/media/media_injector.py`)
- Updated to pass `duration` metadata through the media source chain
- Ensures duration information flows from extraction to AI description

### 6. Comprehensive Tests (`tests/test_video_descriptions.py`)
Created 19 new tests covering:
- Duration extraction from Telegram messages
- Video description generation via Gemini
- Duration limit enforcement (>10 seconds rejected)
- Animated sticker handling
- Error handling (timeouts, unsupported formats)
- Integration with media source pipeline

## Behavior

### Short Videos (≤10 seconds)
- Extracted from Telegram messages with duration metadata
- Downloaded and sent to Gemini 2.0 Flash for analysis
- AI-generated description is cached and displayed in conversation history
- Example: `[media] ‹the video that appears as A person skateboarding in a park on a sunny day›`

### Long Videos (>10 seconds)
- Rejected before consuming description budget
- Status: `UNSUPPORTED`
- Reason: `"too long to analyze (duration: 15s, max: 10s)"`
- Displayed as: `[media] ‹the video that is not understood›`

### Animated Stickers
- Treated as short videos if they have video content
- Duration checked same as regular videos
- Use describe_video() for analysis
- Special handling for `AnimatedEmojies` set (uses emoji name directly)

### Videos Without Duration Metadata
- Accepted for processing (can't enforce limit without metadata)
- May still be analyzed if file is small enough for Gemini

## Models Used

- **Image descriptions**: `gemini-2.0-flash` (unchanged)
- **Video descriptions**: `gemini-2.0-flash` (new)

Both use the same model for consistency, but with different prompts optimized for static vs. moving content.

## Budget Management

Videos and animated stickers follow the same budget rules as images:
- Consume one description budget slot per video
- Long videos (>10s) don't consume budget (rejected before processing)
- Budget exhaustion returns fallback without processing

## Testing

All 114 tests pass, including:
- 19 new video-specific tests
- All existing tests (no regressions)
- Coverage includes:
  - Duration extraction
  - AI description generation
  - Error handling
  - Budget management
  - Media source chain integration

## Future Enhancements

Potential improvements (not in scope for issue #82):
1. Configurable duration limit (currently hardcoded at 10 seconds)
2. Frame sampling for longer videos
3. Support for other video analysis models
4. Video thumbnail extraction for caching

## References

- Issue: https://github.com/olivia3215/cw-telegram-agent/issues/82
- Gemini Video API: Uses REST API with inline video data
- Model: `gemini-2.0-flash` supports both image and video analysis
