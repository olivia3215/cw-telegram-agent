"""
TGS to Video Converter

Converts Telegram animated stickers (TGS files) to video format for LLM analysis.
TGS files are gzip-compressed Lottie animations. This module:
1. Uses python-lottie to parse TGS and export SVG frames
2. Uses cairosvg to convert SVG frames to PNG
3. Uses imageio with bundled ffmpeg to create MP4 video

Requirements:
- Cairo library must be installed on the system
  - macOS: brew install cairo
  - Ubuntu: sudo apt-get install libcairo2-dev
"""

import io
import logging
import tempfile
from pathlib import Path

import cairosvg
import imageio
import numpy as np
from lottie.exporters import svg
from lottie.parsers import tgs
from PIL import Image

logger = logging.getLogger(__name__)


def render_tgs_to_frames(
    tgs_filepath: Path,
    width: int = 512,
    height: int = 512,
    duration: float | None = None,
) -> tuple[list[np.ndarray], float]:
    """
    Render a TGS file to individual frames using python-lottie and cairosvg.

    Args:
        tgs_filepath: Path to the input TGS file
        width: Frame width in pixels
        height: Frame height in pixels
        duration: Maximum duration in seconds (None for full animation)

    Returns:
        Tuple of (list of frames as numpy arrays, fps)

    Raises:
        ValueError: If rendering fails
    """
    try:
        # Load the TGS animation
        animation = tgs.parse_tgs(str(tgs_filepath))

        # Set dimensions
        animation.width = width
        animation.height = height

        # Get animation properties
        fps = animation.frame_rate
        total_frames = int(animation.out_point - animation.in_point)
        anim_duration = total_frames / fps if fps > 0 else 0

        logger.info(
            f"TGS animation: {total_frames} frames, {fps} fps, "
            f"{anim_duration:.2f}s duration, {width}x{height} size"
        )

        # Calculate how many frames to render
        if duration and duration < anim_duration:
            max_frames = int(duration * fps)
            frames_to_render = min(total_frames, max_frames)
        else:
            frames_to_render = total_frames

        # Render all frames
        frames = []
        log_interval = max(1, frames_to_render // 10)  # Log every 10%

        for frame_num in range(frames_to_render):
            if frame_num % log_interval == 0:
                logger.info(f"Rendering frame {frame_num + 1}/{frames_to_render}...")

            # Export frame as SVG to a string buffer
            svg_buffer = io.StringIO()
            svg.export_svg(animation, svg_buffer, frame=frame_num + animation.in_point)
            svg_data = svg_buffer.getvalue()

            # Convert SVG to PNG using cairosvg
            png_data = cairosvg.svg2png(
                bytestring=svg_data.encode("utf-8"),
                output_width=width,
                output_height=height,
            )

            # Convert PNG bytes to numpy array
            pil_image = Image.open(io.BytesIO(png_data))
            frame_array = np.array(pil_image)

            frames.append(frame_array)

        logger.info(f"Successfully rendered {len(frames)} frames from TGS file")
        return frames, fps

    except Exception as e:
        raise ValueError(f"Failed to render TGS to frames: {e}") from e


def create_video_from_frames(
    frames: list[np.ndarray],
    output_path: Path,
    fps: float = 30.0,
) -> Path:
    """
    Create an MP4 video from frame arrays using imageio.

    Args:
        frames: List of frames as numpy arrays (RGB or RGBA format)
        output_path: Path where the MP4 video should be saved
        fps: Frames per second

    Returns:
        Path to the created MP4 file

    Raises:
        ValueError: If video creation fails
    """
    try:
        if not frames:
            raise ValueError("No frames to convert to video")

        # Convert RGBA frames to RGB if needed
        rgb_frames = []
        for frame in frames:
            if frame.shape[2] == 4:  # Has alpha channel
                # Composite onto white background
                alpha = frame[:, :, 3:4] / 255.0
                rgb = frame[:, :, :3]
                white_bg = np.ones_like(rgb) * 255
                composited = (rgb * alpha + white_bg * (1 - alpha)).astype(np.uint8)
                rgb_frames.append(composited)
            else:
                rgb_frames.append(frame[:, :, :3])

        # Write video using imageio's ffmpeg writer
        imageio.mimsave(
            str(output_path),
            rgb_frames,
            fps=fps,
            codec="libx264",
            pixelformat="yuv420p",
            quality=8,
        )

        logger.info(
            f"Created video from {len(rgb_frames)} frames at {fps} fps: {output_path}"
        )
        return output_path

    except Exception as e:
        raise ValueError(f"Failed to create video from frames: {e}") from e


def convert_tgs_to_video(
    tgs_filepath: Path,
    output_path: Path,
    width: int = 512,
    height: int = 512,
    duration: float | None = None,
) -> Path:
    """
    Convert a TGS file to MP4 video.

    Args:
        tgs_filepath: Path to the input TGS file
        output_path: Path where the MP4 video should be saved
        width: Video width in pixels
        height: Video height in pixels
        duration: Maximum duration in seconds (None for full animation)

    Returns:
        Path to the created MP4 file

    Raises:
        ValueError: If conversion fails at any step
    """
    try:
        # Render TGS to frames
        frames, fps = render_tgs_to_frames(tgs_filepath, width, height, duration)

        # Create video from frames
        return create_video_from_frames(frames, output_path, fps)

    except Exception as e:
        raise ValueError(f"Failed to convert TGS to video: {e}") from e


def convert_tgs_to_video_temp(
    tgs_filepath: Path,
    width: int = 512,
    height: int = 512,
    duration: float | None = None,
) -> Path:
    """
    Convert a TGS file to MP4 video using a temporary file.

    Args:
        tgs_filepath: Path to the input TGS file
        width: Video width in pixels
        height: Video height in pixels
        duration: Maximum duration in seconds (None for full animation)

    Returns:
        Path to the created temporary MP4 file

    Raises:
        ValueError: If conversion fails
    """
    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as temp_file:
        temp_path = Path(temp_file.name)

    try:
        return convert_tgs_to_video(tgs_filepath, temp_path, width, height, duration)
    except Exception:
        # Clean up temp file if conversion fails
        if temp_path.exists():
            temp_path.unlink()
        raise


# Test function for development
def test_tgs_conversion(tgs_file_path: Path) -> None:
    """
    Test TGS to video conversion with a sample file.

    Args:
        tgs_file_path: Path to a TGS file to test
    """
    try:
        logger.info(f"Testing TGS to video conversion for: {tgs_file_path}")
        logger.info(f"TGS file size: {tgs_file_path.stat().st_size} bytes")

        # Convert to video
        output_path = convert_tgs_to_video_temp(tgs_file_path)

        logger.info("Video conversion successful!")
        logger.info(f"Output: {output_path}")
        logger.info(f"Video file size: {output_path.stat().st_size} bytes")

        # Clean up
        output_path.unlink()
        logger.info("Test completed successfully")

    except Exception as e:
        logger.error(f"TGS to video conversion test failed: {e}")
        raise


if __name__ == "__main__":
    # Test with a sample TGS file if provided
    import sys

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    if len(sys.argv) > 1:
        tgs_path = Path(sys.argv[1])
        if tgs_path.exists():
            test_tgs_conversion(tgs_path)
        else:
            logger.info(f"File not found: {tgs_path}")
    else:
        logger.info("Usage: python tgs_converter.py <path_to_tgs_file>")
