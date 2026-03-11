# CLAUDE.md — Open-Agent

## Project Overview

Open-Agent (`openagent`) is a lightweight, async-first Python framework for building LLM-powered agents with pluggable provider support. It provides a unified interface across multiple LLM providers (OpenAI, Anthropic, Google Gemini, Ollama) with built-in tool execution, session persistence, MCP integration, and retry logic.

**Python version:** 3.11+
**Build system:** hatchling (PEP 517/518)

## Repository Structure

```
openagent/
├── __init__.py                  # Public API exports
├── core/
│   ├── agent.py                 # Agent class — main orchestrator (run loop, tool dispatch)
│   ├── types.py                 # Canonical types: Message, TextBlock, ToolUseBlock, ToolResultBlock, ToolDef
│   ├── tool.py                  # @tool decorator, ToolRegistry, parameter schema generation
│   ├── session.py               # Session — message history, save/load JSON persistence
│   ├── logging.py               # AgentLogger, configure_logging
│   └── retry.py                 # Exponential backoff retry decorator with provider-specific exceptions
├── provider/
│   ├── base.py                  # BaseProvider ABC (chat, stream methods)
│   ├── converter.py             # MessageConverterMixin — abstract converter pattern
│   ├── anthropic.py             # AnthropicProvider (Claude models)
│   ├── openai.py                # OpenAIProvider (GPT models)
│   ├── google.py                # GoogleProvider (Gemini models)
│   └── ollama.py                # OllamaProvider (local models)
└── mcp.py                       # McpClient — MCP stdio/SSE transport integration

tests/
├── conftest.py                  # Shared fixtures (MockProvider, response helpers)
├── test_agent.py                # Agent initialization, tool calls, parallel execution, max_turns
├── test_tool.py                 # @tool decorator, ToolRegistry, schema generation, async tools
├── test_session.py              # Session management, serialization
├── test_types.py                # Message types and conversions
└── test_ollama_provider.py      # Ollama provider tests

examples/                        # Usage examples (multi-provider, MCP, streaming)
```

## Architecture

### Canonical Message Format

All providers convert to/from a unified `Message` type (`core/types.py`) with typed content blocks:
- `TextBlock` — plain text content
- `ToolUseBlock` — LLM requesting a tool call (with `name`, `arguments`, `id`)
- `ToolResultBlock` — result returned to LLM (with `tool_use_id`, `content`, `is_error`)

### Provider Pattern

Each provider extends `BaseProvider` and implements `async chat()`. Providers use a `MessageConverterMixin` to translate between the canonical `Message` format and provider-specific API formats. Adding a new provider means:
1. Subclass `BaseProvider`
2. Implement a converter mixin
3. Handle tool definitions and tool results in the provider's format

### Agent Loop

`Agent._loop()` runs a turn-based loop (up to `max_turns`):
1. Send messages to provider via `provider.chat()`
2. If response has tool calls, execute them in parallel via `asyncio.gather()`
3. Append tool results to session and repeat
4. If no tool calls, return the text response

### Tool System

- Use `@tool` decorator to mark functions as tools (supports sync and async)
- `ToolRegistry` manages registration and execution
- Parameter schemas are auto-generated from Python type hints and function signatures
- Tools return strings; non-string results are JSON-serialized

### MCP Integration

`McpClient` connects to MCP servers (stdio or SSE transport), discovers tools, and wraps them as callable functions compatible with the tool registry.

## Development Commands

```bash
# Install with all providers and dev dependencies
pip install -e ".[all,dev]"

# Run tests
pytest

# Run tests with verbose output
pytest -v

# Run a specific test file
pytest tests/test_agent.py

# Run a specific test
pytest tests/test_agent.py::test_agent_run
```

## Key Conventions

- **Async-first:** All I/O operations are async. Use `async def` for tools that do I/O.
- **Type hints everywhere:** Use Python 3.11+ style (`str | None`, `list[X]`).
- **Dataclasses for data types:** Core types use `@dataclass`.
- **`from __future__ import annotations`** is used in all source modules.
- **No linter/formatter configured** — follow existing code style (clean, minimal, well-typed).
- **Tests use pytest-asyncio** with `asyncio_mode = "auto"` — async test functions just work.
- **Test files follow `test_*.py` naming** in the `tests/` directory.

## Dependencies

| Package | Purpose |
|---------|---------|
| `mcp` | Model Context Protocol SDK |
| `openai` | OpenAI API client |
| `anthropic` | Anthropic API client |
| `google-genai` | Google Gemini (optional) |
| `ollama` | Ollama local models (optional) |
| `pytest` / `pytest-asyncio` | Testing (dev) |

## Common Patterns

### Adding a tool

```python
from openagent import tool

@tool
def get_weather(city: str) -> str:
    """Get weather for a city."""
    return f"Sunny in {city}"

# Or with custom name/description:
@tool(name="calculator", description="Do math")
def calc(expression: str) -> str:
    return str(eval(expression))
```

### Creating an agent

```python
from openagent import Agent, OpenAIProvider

provider = OpenAIProvider(model="gpt-4o", api_key="...")
agent = Agent(provider=provider, system_prompt="You are helpful.", tools=[get_weather])
result = await agent.run("What's the weather in Paris?")
```

### Adding a new provider

1. Create `openagent/provider/newprovider.py`
2. Subclass `BaseProvider`, implement `async chat()`
3. Create a converter mixin to translate messages
4. Export from `openagent/__init__.py`
5. Add provider-specific retryable exceptions in `core/retry.py`
