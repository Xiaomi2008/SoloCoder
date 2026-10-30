"""Utility functions for agent operations."""

from __future__ import annotations

import json
import re

try:
    import tiktoken
except ImportError:
    tiktoken = None


_CJK = "\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uac00-\ud7af"
_FALLBACK_TOKEN = re.compile(rf"[{_CJK}]|[^\W{_CJK}]+|[^\w\s]")


class _DeterministicFallbackEncoder:
    """A token count without tiktoken, which is an optional dependency.

    Words alone undercount: a BPE tokenizer splits ``build_solocoder_tools``
    or a hash into several tokens, and this counted each as one, so the
    history overflowed while the estimate said it fit. About four characters
    a token is the usual rule for English and code; the larger of the two
    estimates is kept. CJK characters count one each, which is about what
    tokenizers spend on them; as ``\\w`` they ran together into one "word".
    """

    def encode(self, text: str) -> list[str]:
        words = _FALLBACK_TOKEN.findall(text)
        by_length = -(-len(text) // 4)
        if len(words) >= by_length:
            return words
        return [text[i : i + 4] for i in range(0, len(text), 4)]


def _get_encoder(model: str):
    if tiktoken is None:
        return _DeterministicFallbackEncoder()

    # Select appropriate tokenizer based on model
    if "gpt-4o" in model or "gpt-4" in model or "gpt-3.5" in model:
        return tiktoken.get_encoding("cl100k_base")
    if "qwen" in model.lower():
        # Qwen models use similar tokenizer to cl100k_base
        return tiktoken.get_encoding("cl100k_base")
    if "claude" in model.lower() or "anthropic" in model.lower():
        # Claude's tokenizer is not public; cl100k_base is close enough. The
        # "r50base" this asked for does not exist, and the ValueError dropped
        # every Claude count to the character fallback.
        return tiktoken.get_encoding("cl100k_base")
    # Default to cl100k_base as a reasonable approximation
    return tiktoken.get_encoding("cl100k_base")


# What one image costs a vision model, roughly. Providers charge by size (a
# 512px screenshot is ~250 tokens on most; a pasted photo 1-2k); this errs
# high. Counting images as nothing let a session of pasted images and
# screenshots overflow the window before compaction thought it was needed.
IMAGE_TOKEN_ESTIMATE = 1000


def count_tokens_for_messages(messages: list[dict], model: str = "gpt-4o") -> int:
    """Count tokens in a list of messages.

    Args:
        messages: List of message dictionaries with 'role' and 'content' keys
        model: Model name for tokenizer selection (default: gpt-4o)

    Returns:
        Number of tokens in the messages

    Note:
        This is an approximation. Actual token counts may vary slightly.
    """
    try:
        encoder = _get_encoder(model)

        total_tokens = 0

        for msg in messages:
            content = msg.get("content", "")

            # Count tokens per message structure
            # Each message has ~3-4 tokens for role prefix + content tokens
            if isinstance(content, str):
                total_tokens += len(encoder.encode(content))
            elif isinstance(content, list):
                # Handle structured content (tool calls, etc.)
                for block in content:
                    if isinstance(block, dict):
                        block_type = block.get("type", "text")
                        if block_type == "text":
                            text_content = block.get("text", "")
                            total_tokens += len(encoder.encode(text_content))
                        elif block_type in ("image_url", "image"):
                            total_tokens += IMAGE_TOKEN_ESTIMATE
                        elif block_type in ("tool_use", "tool_result"):
                            # Count tool-related tokens (name, arguments, etc.)
                            for key, value in block.items():
                                if isinstance(value, str):
                                    total_tokens += len(encoder.encode(value))
                                elif isinstance(value, (dict, list)):
                                    # A tool call's arguments: write_file's are
                                    # the whole file, and they counted as nothing.
                                    total_tokens += len(encoder.encode(json.dumps(value)))

            # Add ~3-4 tokens per message for role and structure overhead
            total_tokens += 4

        return total_tokens

    except Exception as e:
        # Fallback: rough estimate based on character count
        # Assuming ~4 characters per token (very approximate)
        total_chars = sum(
            len(msg.get("content", "")) if isinstance(msg.get("content"), str) else 0
            for msg in messages
        )
        return max(total_chars // 4, 1)


def estimate_tokens_for_message(message: dict) -> int:
    """Estimate token count for a single message.

    Args:
        message: Message dictionary

    Returns:
        Estimated token count
    """
    content = message.get("content", "")
    if isinstance(content, str):
        return len(content) // 4 + 4  # Rough estimate
    elif isinstance(content, list):
        total = 0
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                text = block.get("text", "")
                total += len(text) // 4
        return max(total + 4, 1)
    return 1
