from openagent.apps.solocoder import CoderAgent, create_coder
from openagent.core.agent import Agent
from openagent.core.display import (
    bold,
    dim,
    blue,
    green,
    yellow,
    red,
    cyan,
    magenta,
    white,
    code,
    user_input,
    diff_addition,
    diff_deletion,
    format_diff_output,
    display_code_block,
    display_diff_claude_style,
    display_tool_call_claude_style,
    display_tool_result_claude_style,
    truncate_text,
    format_file_list,
    format_grep_results_claude_style,
    display_claude_code_block,
)
from openagent.core.logging import AgentLogger, configure_logging, logger
from openagent.infrastructure import McpClient
from openagent.runtime import (
    AgentResult,
    ContextCompactionCompleted,
    ContextCompactionFailed,
    ContextCompactionStarted,
    MessageCompleted,
    MessageDelta,
    MessageFailed,
    MessageStarted,
    RunCancelled,
    RunCompleted,
    RunFailed,
    RunStarted,
    RuntimeEvent,
    ToolCallCompleted,
    ToolCallFailed,
    ToolCallStarted,
)
from openagent.provider.anthropic import AnthropicProvider
from openagent.provider.base import BaseProvider
from openagent.provider.google import GoogleProvider
from openagent.provider.ollama import OllamaProvider
from openagent.provider.openai import OpenAIProvider
from openagent.core.session import Session
from openagent.core.tool import ToolRegistry, tool
from openagent.model import (
    ContentBlock,
    Message,
    TextBlock,
    ToolDef,
    ToolResultBlock,
    ToolUseBlock,
)

__all__ = [
    # Core classes
    "Agent",
    "CoderAgent",
    "Session",
    "McpClient",
    "ToolRegistry",
    # Providers
    "AnthropicProvider",
    "BaseProvider",
    "GoogleProvider",
    "OllamaProvider",
    "OpenAIProvider",
    # Logging
    "AgentLogger",
    "configure_logging",
    "logger",
    # Runtime events
    "AgentResult",
    "ContextCompactionCompleted",
    "ContextCompactionFailed",
    "ContextCompactionStarted",
    "MessageCompleted",
    "MessageDelta",
    "MessageFailed",
    "MessageStarted",
    "RunCancelled",
    "RunCompleted",
    "RunFailed",
    "RunStarted",
    "RuntimeEvent",
    "ToolCallCompleted",
    "ToolCallFailed",
    "ToolCallStarted",
    # Types
    "ContentBlock",
    "Message",
    "TextBlock",
    "ToolDef",
    "ToolResultBlock",
    "ToolUseBlock",
    # Decorators and helpers
    "tool",
    "create_coder",
    # Display utilities (Claude Code style)
    "bold",
    "dim",
    "blue",
    "green",
    "yellow",
    "red",
    "cyan",
    "magenta",
    "white",
    "code",
    "diff_addition",
    "diff_deletion",
    "format_diff_output",
    "display_code_block",
    "display_diff_claude_style",
    "display_tool_call_claude_style",
    "display_tool_result_claude_style",
    "truncate_text",
    "user_input",
    "format_file_list",
    "format_grep_results_claude_style",
    "display_claude_code_block",
]
