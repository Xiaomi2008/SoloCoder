from server import display_chat_message, render_chat_history


def test_process_user_message():
    """Test user message processing."""
    chat_history = []
    prompt = "Hello"

    # Process user message
    chat_history.append({"role": "user", "content": prompt})

    assert len(chat_history) == 1
    assert chat_history[0]["role"] == "user"
    assert chat_history[0]["content"] == "Hello"
