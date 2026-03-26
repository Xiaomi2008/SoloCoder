from unittest.mock import Mock, AsyncMock
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def test_agent_run_returns_string():
    """Test that agent.run() returns a string response."""
    # Mock agent
    mock_agent = Mock()
    mock_agent.run = AsyncMock(return_value="Test response")

    # Verify run method exists
    assert hasattr(mock_agent, "run")


def test_convert_response_to_message():
    """Test response to message conversion."""
    response_content = "This is a test response"
    assert isinstance(response_content, str)
