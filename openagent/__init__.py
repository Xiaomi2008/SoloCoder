"""OpenAgent — async-first agent framework with pluggable LLM providers.

Public API surface. Keep this list explicit: it is what `from openagent import *`
and the docs describe, and it is the contract the Mac app's service layer builds on.
"""

from __future__ import annotations

from openagent.apps.solocoder import CoderAgent, create_coder
from openagent.core.agent import Agent
from openagent.core.display import (
    blue,
    bold,
    cyan,
    code,
    diff_addition,
    diff_deletion,
    dim,
    display_claude_code_block,
    display_code_block,
    display_diff_claude_style,
    display_tool_call_claude_style,
    display_tool_result_claude_style,
    format_diff_output,
    format_file_list,
    format_grep_results_claude_style,
    green,
    magenta,
    red,
    truncate_text,
    user_input,
    white,
    yellow,
)
from openagent.core.logging import AgentLogger, configure_logging, logger
from openagent.core.memory import (
    FactMemory,
    MemoryStore,
    PatternMemory,
    PreferenceMemory,
    get_memory_store,
)
from openagent.core.learning_extractor import LearningExtractor
from openagent.core.session import Session
from openagent.core.tool import ToolContext, ToolRegistry, tool
from openagent.infrastructure import McpClient
from openagent.model import (
    ContentBlock,
    Message,
    TextBlock,
    ToolDef,
    ToolResultBlock,
    ToolUseBlock,
)
from openagent.provider import (
    AnthropicProvider,
    BaseProvider,
    GoogleProvider,
    OllamaProvider,
    OpenAIProvider,
)
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
    RuntimeAgent,
    RuntimeEvent,
    ToolCallCompleted,
    ToolCallFailed,
    ToolCallStarted,
)

__all__ = [
    # Core classes
    "Agent",
    "RuntimeAgent",
    "CoderAgent",
    "Session",
    "McpClient",
    "ToolContext",
    "ToolRegistry",
    # Memory and learning
    "MemoryStore",
    "PatternMemory",
    "PreferenceMemory",
    "FactMemory",
    "get_memory_store",
    "LearningExtractor",
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
    "format_file_list",
    "format_grep_results_claude_style",
    "display_code_block",
    "display_claude_code_block",
    "display_diff_claude_style",
    "display_tool_call_claude_style",
    "display_tool_result_claude_style",
    "truncate_text",
    "user_input",
]
