#!/usr/bin/env python3
"""SoloCoder Web UI - Streamlit interface for the Coder Agent with vision support."""

from __future__ import annotations

import asyncio
import base64
import os
from io import BytesIO
from openagent import configure_logging, OpenAIProvider
from openagent.coder import CoderAgent

import streamlit as st

# Page configuration
st.set_page_config(
    page_title="SoloCoder",
    page_icon="💻",
    layout="wide",
    initial_sidebar_state="expanded",
)


def format_error_message(error: Exception) -> str:
    """Format error messages for display."""
    error_str = str(error)
    if "OpenAI" in error_str:
        return f"OpenAI API Error: {error_str.split(': ', 1)[-1] if ': ' in error_str else error_str}"
    elif "401" in error_str or "Unauthorized" in error_str:
        return "❌ Authentication failed. Please check your API key."
    elif "429" in error_str or "Rate limit" in error_str:
        return "⚠️ Rate limit exceeded. Please wait a moment and try again."
    else:
        return f"⚠️ Error: {error_str}"


def create_agent(model: str, api_key: str | None) -> CoderAgent:
    """Create CoderAgent with specified settings (sync function)."""
    configure_logging(level=30)  # WARNING level

    provider = OpenAIProvider(model=model, api_key=api_key)

    return CoderAgent(
        provider=provider,
        max_turns=100,
        working_dir=None,
        max_context_tokens=128000,
        compact_threshold=0.8,
        disable_compaction=False,
    )


def display_chat_message(message: str, is_user: bool = False):
    """Display a chat message with appropriate styling."""
    if is_user:
        with st.chat_message("user"):
            st.markdown(message)
    else:
        with st.chat_message("assistant"):
            st.markdown(message)


def render_chat_history():
    """Render all messages in chat history."""
    for message in st.session_state.chat_history:
        display_chat_message(message["content"], is_user=message["role"] == "user")


def handle_agent_response(
    prompt: str,
    image_data: str | None = None,
) -> bool:
    """Process user prompt through agent with optional image input.

    Args:
        prompt: Text prompt from user
        image_data: Optional base64-encoded image data for vision models

    Returns:
        True if successful, False if error occurred
    """
    try:
        # Use multimodal if image provided
        if image_data:
            response = asyncio.run(
                st.session_state.agent.run_multimodal(text=prompt, image_data=image_data)
            )
        else:
            response = asyncio.run(st.session_state.agent.run(prompt))

        st.session_state.chat_history.append(
            {"role": "assistant", "content": str(response)}
        )

        st.session_state.turn_counter += 1

        return True

    except Exception as e:
        error_msg = format_error_message(e)
        st.session_state.chat_history.append(
            {"role": "assistant", "content": error_msg}
        )
        return False


def render_sidebar():
    """Render sidebar with controls and agent status."""
    with st.sidebar:
        st.header("⚙️ Settings")

        # API key input
        api_key = st.text_input("🔑 API Key", type="password", key="api_key_input")

        # Model selection
        model = st.text_input("🤖 Model", value=st.session_state.model)

        st.divider()

        # Agent status
        if st.session_state.agent:
            st.success("✅ Agent ready!")

            if st.button("🔄 New Session"):
                st.session_state.chat_history = []
                st.session_state.turn_counter = 0
                st.session_state.agent = None
                st.session_state.api_key = None
                st.rerun()

            # Show turn counter
            st.write(f"📊 Turns: {st.session_state.turn_counter}")
        else:
            st.info("💡 Click 'Start Session' to begin")

        st.divider()

        # Start button
        if not st.session_state.agent and api_key:
            if st.button("🚀 Start Session"):
                try:
                    with st.spinner("⏳ Initializing agent..."):
                        st.session_state.agent = create_agent(model, api_key)
                        st.session_state.api_key = api_key
                        st.session_state.chat_history = []
                        st.session_state.turn_counter = 0
                        st.rerun()
                except Exception as e:
                    st.error(format_error_message(e))

    return model, api_key


def main():
    st.title("💻 SoloCoder")
    st.write("A chat-based coding assistant powered by OpenAgent")

    # Initialize session state
    if "chat_history" not in st.session_state:
        st.session_state.chat_history = []

    if "agent" not in st.session_state:
        st.session_state.agent = None

    if "turn_counter" not in st.session_state:
        st.session_state.turn_counter = 0

    if "model" not in st.session_state:
        st.session_state.model = "gpt-4o"

    if "api_key" not in st.session_state:
        st.session_state.api_key = None

    # Render sidebar and get inputs
    model, api_key = render_sidebar()

    # Render chat history
    render_chat_history()

    # User input area
    st.divider()

    # Chat input area with optional image upload
    st.divider()

    # Image upload for vision analysis
    uploaded_image = st.file_uploader(
        "📷 Upload an image (optional, for vision analysis)",
        type=["png", "jpg", "jpeg"],
        key="image_uploader",
        help="Upload an image for Qwen3.5 vision analysis",
    )

    # Chat input
    prompt = st.chat_input("Ask SoloCoder to help with code...", key="chat_input")

    if prompt or (uploaded_image and st.session_state.agent):
        # Process uploaded image if any
        image_data: str | None = None
        if uploaded_image:
            try:
                image_bytes = uploaded_image.read()
                image_data = base64.b64encode(image_bytes).decode("utf-8")

                # Show uploaded image preview
                st.image(image_bytes, caption="Uploaded image for analysis", use_container_width=True)
            except Exception as e:
                st.error(f"Error processing image: {e}")
                uploaded_image = None  # Reset uploader on error

        # Add user message to history (include image if uploaded)
        if image_data:
            st.session_state.chat_history.append({
                "role": "user",
                "content": prompt or "[Image uploaded]",
                "has_image": True,
            })
        else:
            st.session_state.chat_history.append({"role": "user", "content": prompt})
        display_chat_message(prompt or "Image uploaded", is_user=True)

        # Process through agent if agent is ready
        if st.session_state.agent:
            with st.spinner("⏳ Thinking..."):
                success = handle_agent_response(prompt, image_data)

            if success:
                display_chat_message(
                    st.session_state.chat_history[-1]["content"], is_user=False
                )


if __name__ == "__main__":
    main()
