#!/usr/bin/env python3
"""SoloCoder Web UI - Streamlit interface for the Coder Agent."""

from __future__ import annotations

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


def main():
    st.title("💻 SoloCoder")
    st.write("A chat-based coding assistant powered by OpenAgent")


if __name__ == "__main__":
    main()
