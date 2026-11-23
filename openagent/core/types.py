from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Literal, Union


@dataclass
class TextBlock:
    text: str
    type: Literal["text"] = "text"


@dataclass
class ImageBlock:
    """Image block for vision-capable models like Qwen3.5-VL.

    Supports base64-encoded images in the format:
    {"type": "image_url", "image_url": {"url": "data:image/png;base64,..."}}
    """
    data: str  # base64-encoded image data
    mime_type: str = "image/png"
    type: Literal["image_url"] = "image_url"

    @property
    def url(self) -> str:
        """Return the full data URI for the image."""
        return f"data:{self.mime_type};base64,{self.data}"


@dataclass
class VideoBlock:
    """A video for a model that reads video, held as its path.

    The file is read when a request is sent, so a stored session stays small
    and a video is sent as it is now.
    """
    path: str
    mime_type: str = "video/mp4"
    #: Frames a second and the most frames to sample; None leaves it to the server.
    fps: float | None = None
    max_frames: int | None = None
    type: Literal["video"] = "video"

    def data_url(self) -> str:
        """The file as a data: URL. OSError when it cannot be read."""
        import base64
        from pathlib import Path

        data = base64.b64encode(Path(self.path).read_bytes()).decode("ascii")
        return f"data:{self.mime_type};base64,{data}"


@dataclass
class ToolUseBlock:
    name: str
    arguments: dict[str, Any]
    id: str = field(default_factory=lambda: f"call_{uuid.uuid4().hex[:24]}")
    type: Literal["tool_use"] = "tool_use"
    # Why the arguments could not be read, for a call that must not run.
    invalid: str | None = None


@dataclass
class ToolResultBlock:
    tool_use_id: str
    content: str
    tool_name: str | None = None
    is_error: bool = False
    type: Literal["tool_result"] = "tool_result"


ContentBlock = Union[TextBlock, ImageBlock, VideoBlock, ToolUseBlock, ToolResultBlock]


@dataclass
class Message:
    role: Literal["system", "user", "assistant", "tool_result"]
    content: Union[str, list[ContentBlock]]

    @property
    def text(self) -> str:
        """Get all text content, excluding images."""
        if isinstance(self.content, str):
            return self.content
        parts: list[str] = []
        for block in self.content:
            if isinstance(block, (TextBlock, ImageBlock)):
                if isinstance(block, TextBlock):
                    parts.append(block.text)
        return "\n".join(parts)

    @property
    def tool_calls(self) -> list[ToolUseBlock]:
        if isinstance(self.content, str):
            return []
        return [b for b in self.content if isinstance(b, ToolUseBlock)]

    @property
    def tool_results(self) -> list[ToolResultBlock]:
        if isinstance(self.content, str):
            return []
        return [b for b in self.content if isinstance(b, ToolResultBlock)]

    @property
    def has_tool_calls(self) -> bool:
        return len(self.tool_calls) > 0

    @property
    def has_images(self) -> bool:
        """Check if the message contains image blocks."""
        if isinstance(self.content, str):
            return False
        return any(isinstance(b, ImageBlock) for b in self.content)

    @property
    def has_videos(self) -> bool:
        if isinstance(self.content, str):
            return False
        return any(isinstance(b, VideoBlock) for b in self.content)


@dataclass
class ToolDef:
    name: str
    description: str
    parameters: dict[str, Any]


def text_message(role: Literal["user", "assistant", "system"], text: str) -> Message:
    return Message(role=role, content=text)


def assistant_message(content: list[ContentBlock]) -> Message:
    return Message(role="assistant", content=content)


def tool_result_message(results: list[ToolResultBlock]) -> Message:
    return Message(role="tool_result", content=results)
