"""Let a model that reads video watch a file.

The video reaches the model in the message after the tool result, as a
``VideoBlock`` that holds the path; the provider reads the file when it sends
the request. A data: URL is the one source a server on another machine can
read: basalt reads a path only for a request from its own machine.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

from openagent.core.tool import ToolContext, tool
from openagent.core.types import VideoBlock
from openagent.tools.builtin import _scope

VIDEO_TYPES = {
    ".mp4": "video/mp4",
    ".m4v": "video/mp4",
    ".mov": "video/quicktime",
    ".webm": "video/webm",
    ".mkv": "video/x-matroska",
    ".avi": "video/x-msvideo",
}
#: basalt takes a 256 MiB request; base64 makes a file a third larger, and the
#: rest of the conversation rides in the same request.
MAX_VIDEO_BYTES = 180 << 20
_EXTRA_BIN_DIRS = ("/opt/homebrew/bin", "/usr/local/bin")


def video_block(
    path: str,
    fps: float | None = None,
    max_frames: int | None = None,
    tool_context: ToolContext | None = None,
) -> VideoBlock:
    """The block for a video the model asked to watch; ValueError says why there is none."""
    file_path, denied = _scope(tool_context or ToolContext(), path)
    if denied:
        raise ValueError(denied.removeprefix("Error: "))
    assert file_path is not None
    if not file_path.is_file():
        raise ValueError(f"'{path}' is not a file.")
    mime_type = VIDEO_TYPES.get(file_path.suffix.lower())
    if mime_type is None:
        raise ValueError(f"'{path}' is not a video: use {', '.join(sorted(VIDEO_TYPES))}.")
    size = file_path.stat().st_size
    if size > MAX_VIDEO_BYTES:
        raise ValueError(
            f"'{path}' is {size / 2**20:.0f} MiB, over the {MAX_VIDEO_BYTES >> 20} MiB a request can carry: "
            "cut or shrink it first (ffmpeg)."
        )
    if fps is not None and not 0 < float(fps) <= 60:
        raise ValueError("fps must be above 0 and at most 60.")
    if max_frames is not None and not 4 <= int(max_frames) <= 768:
        raise ValueError("max_frames must be from 4 to 768.")
    return VideoBlock(
        path=str(file_path),
        mime_type=mime_type,
        fps=float(fps) if fps is not None else None,
        max_frames=int(max_frames) if max_frames is not None else None,
    )


def _probe(path: str) -> str:
    """Duration, size and frame rate from ffprobe, or nothing when it is missing."""
    search = ":".join(_EXTRA_BIN_DIRS)
    ffprobe = shutil.which("ffprobe") or shutil.which("ffprobe", path=search)
    if ffprobe is None:
        return ""
    try:
        out = subprocess.run(
            [ffprobe, "-v", "error", "-select_streams", "v:0", "-show_entries",
             "stream=width,height,avg_frame_rate:format=duration", "-of", "json", path],
            capture_output=True, text=True, timeout=15, check=True,
        ).stdout
        info: dict[str, Any] = json.loads(out)
        stream = (info.get("streams") or [{}])[0]
        parts = []
        if info.get("format", {}).get("duration"):
            parts.append(f"{float(info['format']['duration']):.1f} s")
        if stream.get("width"):
            parts.append(f"{stream['width']}x{stream['height']}")
        num, _, den = str(stream.get("avg_frame_rate", "")).partition("/")
        if num and den and float(den):
            parts.append(f"{float(num) / float(den):.0f} fps")
        return ", ".join(parts)
    except (OSError, subprocess.SubprocessError, ValueError, KeyError, IndexError):
        return ""


@tool
def view_video(
    path: str,
    fps: float | None = None,
    max_frames: int | None = None,
    tool_context: ToolContext | None = None,
) -> str:
    """Watch a video file: its frames are attached in the next message. The sound is not.

    Args:
        path: The video file (mp4, mov, webm, mkv, avi), absolute or relative to the working directory
        fps: Frames to sample a second (default 2); lower it for a long video
        max_frames: The most frames to sample, 4 to 768

    Returns:
        What the video is, or why it cannot be shown
    """
    try:
        block = video_block(path, fps, max_frames, tool_context)
    except ValueError as exc:
        return f"Error: {exc}"
    size = Path(block.path).stat().st_size / 2**20
    details = ", ".join(p for p in (_probe(block.path), f"{size:.1f} MiB") if p)
    return f"Video {block.path} ({details}): its frames are attached in the next message."


video_tools = [view_video]

__all__ = ["MAX_VIDEO_BYTES", "VIDEO_TYPES", "video_block", "video_tools", "view_video"]
