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
