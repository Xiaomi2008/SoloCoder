# SoloCoder - Project Guidelines

## Overview

SoloCoder is a Claude Code-style CLI coding assistant that runs entirely locally. It's built on **OpenAgent**, a lightweight async-first Python framework for building LLM-powered agents with pluggable provider support.

**Python version:** 3.11+
**Build system:** hatchling (PEP 517/518)
**Package manager:** uv (recommended for all Python operations)

## Repository Structure

```
SoloCoder/
├── cli_coder.py              # Main CLI entry point with interactive session
├── openagent/                # Agent framework package
│   ├── __init__.py           # Public API exports
│   ├── coder.py              # CoderAgent - specialized coding agent
│   ├── core/                 # Core components
│   │   ├── agent.py          # Agent class — main orchestrator (run loop, tool dispatch)
│   │   ├── types.py          # Canonical types: Message, TextBlock, ToolUseBlock, etc.
│   │   ├── tool.py           # @tool decorator, ToolRegistry, parameter schema generation
│   │   ├── session.py        # Session — message history, save/load JSON persistence
│   │   ├── logging.py        # AgentLogger, configure_logging
│   │   ├── retry.py          # Exponential backoff retry logic
│   │   ├── display.py        # Output formatting and display utilities
│   │   ├── bash_manager.py   # Shell command execution manager
│   │   ├── task_manager.py   # Task tracking and management
│   │   └── skill_manager.py  # Skill/command system
│   ├── provider/             # LLM providers
│   │   ├── base.py           # BaseProvider ABC (chat, stream methods)
│   │   ├── converter.py      # MessageConverterMixin — abstract converter pattern
│   │   ├── openai.py         # OpenAIProvider (GPT models, LM Studio)
│   │   ├── anthropic.py      # AnthropicProvider (Claude models)
│   │   ├── google.py         # GoogleProvider (Gemini models)
│   │   └── ollama.py         # OllamaProvider (local models)
│   ├── tools/                # Built-in tool implementations
│   │   ├── __init__.py       # Tool exports
│   │   └── builtin.py        # File, shell, search tool implementations
│   └── mcp.py                # McpClient — MCP stdio/SSE transport integration
├── tests/                    # Test suite location
├── examples/                 # Usage examples (multi-provider, MCP, streaming)
├── dev_docs/                 # Development documentation
│   └── architecture.md       # Architecture details
├── CLAUDE.md                 # This file — project guidelines for Claude
└── README.md                 # User-facing documentation
```

## Development Commands

### Python Package Management (uv recommended)

```bash
# Install the package with all dependencies
uv pip install -e ".[all,dev]"

# Run tests using uv
uv run pytest

# Run a specific test file
uv run pytest tests/test_agent.py

# Run a specific test
uv run pytest tests/test_agent.py::test_agent_run

# Add new dependency
uv add package-name

# Add dev dependency
uv add --group dev package-name
```

### Alternative: Using pip directly

```bash
# Install the package with all dependencies
pip install -e ".[all,dev]"

# Run tests
pytest
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
| `duckduckgo-search`, `httpx` | Web search capabilities (optional) |
| `pytest`, `pytest-asyncio` | Testing (dev) |

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
