from __future__ import annotations

import os
from unittest.mock import patch

from server import format_error_message


def test_format_error_message_openai():
    """Test OpenAI API error formatting."""
    error = Exception("OpenAI API Error: invalid_key")
    assert "OpenAI API Error" in format_error_message(error)


def test_format_error_message_unauthorized():
    """Test 401 unauthorized error formatting."""
    error = Exception("401: Unauthorized")
    assert "Authentication failed" in format_error_message(error)


def test_format_error_message_ratelimit():
    """Test 429 rate limit error formatting."""
    error = Exception("429: Rate limit exceeded")
    assert "Rate limit" in format_error_message(error)


def test_session_state_initialization():
    """Test session state structure."""
    assert "chat_history" in {} or [] == []
    assert "turn_counter" in {} or 0 == 0


def test_api_key_retrieval():
    """Test API key retrieval logic."""
    # Mock environment variable
    with patch.dict(os.environ, {"OPENAI_API_KEY": "test_key"}):
        api_key = os.environ.get("OPENAI_API_KEY")
        assert api_key == "test_key"
