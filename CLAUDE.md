# OpenAgent (SoloCoder) - Project Guidelines

## Overview
OpenAgent is a lightweight, async-first Python framework for building LLM-powered agents with pluggable provider support. It provides a unified interface across multiple LLM providers (OpenAI, Anthropic, Google Gemini, Ollama, Qwen) with built-in tool execution, session persistence, MCP integration, retry logic, and vision-based computer control capabilities.

**Python version:** 3.11+
**Build system:** hatchling (PEP 517/518)
**Package manager:** uv (recommended for all Python operations)

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
├── tools/
│   ├── __init__.py              # Tool exports
│   ├── computer_use.py          # Vision-based macOS control (screenshot, click, type_text)
│   └── ...                      # Other built-in tools
└── mcp.py                       # McpClient — MCP stdio/SSE transport integration

tests/                           # Test suite location
examples/                        # Usage examples (multi-provider, MCP, streaming)
cli_coder.py                     # Coder Agent CLI with interactive features
docs/                            # Documentation (computer-use-guide.md)
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
| `pyautogui`, `pillow` | Computer use - mouse/keyboard control and screenshots (optional) |
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

### Using Computer Use (Vision-Based Control)

Requires a multimodal model like Qwen3.5-35B-A3B:

```python
from openagent import Agent, OpenAIProvider
from openagent.tools.computer_use import computer_tools  # screenshot, click, type_text, etc.

provider = OpenAIProvider(
    model="qwen3.5-35b-a3b",
    base_url="http://localhost:1234/v1"
)
agent = Agent(
    provider=provider,
    system_prompt="You can control the Mac computer using screenshots and mouse/keyboard actions.",
    tools=computer_tools
)
result = await agent.run("Open Safari and navigate to github.com")
```

See `docs/computer-use-guide.md` for detailed usage.

### Adding a new provider

1. Create `openagent/provider/newprovider.py`
2. Subclass `BaseProvider`, implement `async chat()`
3. Create a converter mixin to translate messages
4. Export from `openagent/__init__.py`
5. Add provider-specific retryable exceptions in `core/retry.py`
