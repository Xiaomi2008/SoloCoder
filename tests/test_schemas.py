import pytest
from datetime import datetime
import sys

sys.path.insert(0, "backend")
from api.schemas import (
    Message,
    ToolCall,
    ToolResult,
    ChatRequest,
    ChatResponse,
    MessageRole,
    ToolName,
    ModelInfo,
    TokenUsage,
    PaginatedChatResponse,
    SessionStatus,
)


def test_message_role_enum():
    """Test MessageRole enum."""
    assert MessageRole.user.value == "user"
    assert MessageRole.assistant.value == "assistant"
    assert MessageRole.tool.value == "tool"


def test_chat_request_validation():
    """Test ChatRequest validation."""
    request = ChatRequest(message="Hello")
    assert request.message == "Hello"


def test_model_info():
    """Test ModelInfo schema."""
    model = ModelInfo(
        id="gpt-4o",
        name="GPT-4o",
        provider="OpenAI",
        description="OpenAI flagship model",
    )
    assert model.id == "gpt-4o"


def test_paginated_response():
    """Test PaginatedChatResponse structure."""
    messages = [Message(id="1", role=MessageRole.user, content="Hi")]
    response = PaginatedChatResponse(
        messages=messages, total=1, has_more=False, limit=50, offset=0
    )
    assert response.total == 1
    assert response.has_more is False
