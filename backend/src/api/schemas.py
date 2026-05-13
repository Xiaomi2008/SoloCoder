from pydantic import BaseModel, Field
from typing import List, Optional
from datetime import datetime
from enum import Enum


class MessageRole(str, Enum):
    """Message role types."""

    user = "user"
    assistant = "assistant"
    tool = "tool"


class ToolName(str, Enum):
    """Available tool names."""

    read = "read"
    write = "write"
    edit = "edit"
    bash = "bash"
    glob = "glob"
    grep = "grep"
    web_search = "web_search"
    web_fetch = "web_fetch"
    todo_write = "todo_write"
    task = "task"


class Message(BaseModel):
    """Chat message schema."""

    id: str
    role: MessageRole
    content: str
    timestamp: datetime = Field(default_factory=datetime.now)


class ToolCall(BaseModel):
    """Tool call schema."""

    id: str
    name: ToolName
    arguments: dict


class ToolResult(BaseModel):
    """Tool result schema."""

    tool_use_id: str
    content: Optional[str] = None
    is_error: bool = False


class ChatRequest(BaseModel):
    """Chat request schema."""

    message: str
    model: Optional[str] = None


class ChatResponse(BaseModel):
    """Chat response schema."""

    message: Message
    tool_calls: List[ToolCall] = []
    tool_results: List[ToolResult] = []


class TokenUsage(BaseModel):
    """Token usage statistics."""

    tokens: int
    max_tokens: int
    percentage: float


class ModelInfo(BaseModel):
    """Model information."""

    id: str
    name: str
    provider: str
    description: str


class ModelsResponse(BaseModel):
    """Models list response."""

    models: List[ModelInfo]


class SessionStatus(BaseModel):
    """Session status info."""

    turn_count: int
    model: str
    token_count: Optional[int] = None


class PaginatedChatResponse(BaseModel):
    """Paginated chat response schema."""

    messages: List[Message]
    total: int
    has_more: bool
    limit: int
    offset: int
