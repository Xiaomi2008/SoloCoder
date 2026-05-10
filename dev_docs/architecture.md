# OpenAgent Architecture Documentation

This document provides a comprehensive overview of the OpenAgent framework architecture, including module structure, design patterns, data flow, and integration points.

## Table of Contents

1. [High-Level Overview](#high-level-overview)
2. [Module Structure](#module-structure)
3. [Core Components](#core-components)
4. [Provider System](#provider-system)
5. [Tool System](#tool-system)
6. [Data Flow](#data-flow)
7. [Design Patterns](#design-patterns)
8. [Integration Points](#integration-points)

---

## High-Level Overview

OpenAgent is a lightweight, async-first agent framework with pluggable LLM providers and comprehensive built-in tools. It follows a modular architecture that separates concerns into distinct layers:

```
┌─────────────────────────────────────────────────────────────────┐
│                        OPENAGENT FRAMEWORK                       │
│                                                                  │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐       │
│  │   Public API │    │  CoderAgent  │    │   Session    │       │
│  │   Exports    │    │ Specialized  │    │ Persistence  │       │
│  └──────────────┘    └──────────────┘    └──────────────┘       │
│                                                                  │
│  ┌─────────────────────────────────────────────────────────┐     │
│  │                    CORE LAYER                           │     │
│  │  Agent Orchestrator | Tool Registry | Task Manager      │     │
│  └─────────────────────────────────────────────────────────┘     │
│                                                                  │
│  ┌─────────────────────────────────────────────────────────┐     │
│  │                  PROVIDER LAYER                         │     │
│  │   OpenAI | Anthropic | Google | Ollama | LMStudio      │     │
│  └─────────────────────────────────────────────────────────┘     │
│                                                                  │
│  ┌─────────────────────────────────────────────────────────┐     │
│  │                   TOOLS LAYER                           │     │
│  │   File Ops | Shell | Web Search | Task Mgmt | Planning  │     │
│  └─────────────────────────────────────────────────────────┘     │
└─────────────────────────────────────────────────────────────────┘
```

---

## Module Structure

### Project Layout

```
openagent/
├── __init__.py                  # Public API exports
├── apps/                        # Application packages
│   └── solocoder/               # SoloCoder app composition
├── infrastructure/              # MCP and shell infrastructure helpers
├── model/                       # Canonical message and tool block exports
├── runtime/                     # Runtime agent, context, and event surfaces
├── coder.py                     # SoloCoder compatibility shim
├── mcp.py                       # MCP compatibility shim
├── core/                        # Core framework components
│   ├── agent.py                 # Agent class — main orchestrator
│   ├── types.py                 # Canonical types: Message, ToolUseBlock, etc.
│   ├── tool.py                  # @tool decorator and ToolRegistry
│   ├── session.py               # Session management and persistence
│   ├── logging.py               # Logging configuration
│   ├── retry.py                 # Retry logic with exponential backoff
│   ├── display.py               # Claude Code style output formatting
│   ├── bash_manager.py          # Background bash session management
│   ├── task_manager.py          # TODO/task tracking system
│   └── skill_manager.py         # Skills and slash command registry
├── provider/                    # Provider implementations and compatibility imports
│   ├── base.py                  # BaseProvider ABC
│   ├── anthropic.py             # Anthropic/Claude support
│   ├── openai.py                # OpenAI/GPT support
│   ├── google.py                # Google/Gemini support
│   ├── ollama.py                # Ollama/local models support
│   └── converter.py             # Response format converters
├── providers/                   # Shared provider event types
├── tools/                       # Built-in tool implementations
│   ├── __init__.py              # Tool exports
│   └── builtin.py               # All built-in tools
└── example*.py                  # Usage examples
```

### Public Namespaces

- `openagent.model` is the canonical import surface for message and tool block types.
- `openagent.runtime` exposes the runtime agent, runtime context, and runtime event/result types.
- `openagent.providers` currently hosts shared provider stream event types.
- `openagent.provider` remains the compatibility import path for provider implementations.
- `openagent.infrastructure` exposes MCP and shell-adjacent infrastructure helpers.
- `openagent.apps.solocoder` contains the SoloCoder app composition. Top-level `openagent`, `openagent.coder`, and `openagent.mcp` still provide compatibility re-exports where needed.

---

## Core Components

### 1. Agent Class (`openagent/core/agent.py`)

The main orchestrator that coordinates the agent loop:

**Responsibilities:**
- Manages conversation state via Session
- Registers and executes tool calls
- Coordinates with LLM providers
- Handles max turn limits
- Integrates MCP clients for external tools

**Key Methods:**
```python
async def run(self, user_input: str) -> str
    """Main entry point - processes user input through agent loop."""

@property
def messages(self) -> list[Message]
    """Access to conversation history."""
```

### 2. Type System (`openagent/core/types.py`)

Canonical data structures for the framework:

| Type | Description |
|------|-------------|
| `Message` | Conversation message with role and content |
| `TextBlock` | Text content block |
| `ToolUseBlock` | Tool invocation request from LLM |
| `ToolResultBlock` | Tool execution result |
| `ContentBlock` | Union of all content types |
| `ToolDef` | Tool definition for provider APIs |

**Message Flow:**
```python
@dataclass
class Message:
    role: Literal["system", "user", "assistant", "tool_result"]
    content: Union[str, list[ContentBlock]]
    
    @property
    def tool_calls(self) -> list[ToolUseBlock]  # Extract tool calls
    @property
    def text(self) -> str                        # Extract text content
```

### 3. Tool Registry (`openagent/core/tool.py`)

Central registry for discovering and executing tools:

**Features:**
- Automatic schema generation from function signatures
- Async/sync function support
- Error handling and result formatting
- Tool discovery via `@tool` decorator

**Registration Flow:**
```python
@tool
def my_tool(param: str) -> str:
    """Tool description."""
    return "result"

# Automatically registered with metadata:
# - _tool_name = "my_tool"
# - _tool_description = "Tool description."
# - _tool_parameters = {"type": "object", "properties": {...}}
```

### 4. Session Management (`openagent/core/session.py`)

Conversation state persistence:

**Capabilities:**
- In-memory conversation history
- JSON file save/load
- System prompt management
- Tool result tracking

```python
session = Session(system_prompt="You are helpful.")
session.add("user", "Hello!")
session.add("assistant", "Hi there!")
session.save("conversation.json")  # Persist to disk
restored = Session.load("conversation.json")  # Restore later
```

### 5. Bash Manager (`openagent/core/bash_manager.py`)

Background process management:

**Features:**
- Persistent bash sessions with session IDs
- Output retrieval (full or tail)
- Process termination
- Working directory isolation

```python
# Start background session
session_id = await manager.start_session("npm run test")

# Retrieve output
output = manager.get_output(session_id, tail_lines=50)

# Terminate
await manager.kill_session(session_id)
```

### 6. Task Manager (`openagent/core/task_manager.py`)

TODO tracking system:

**Status Flow:**
```
pending → in_progress → completed
                    ↓
                 deleted
```

**API:**
```python
manager = get_task_manager()

# Create tasks
task_id = manager.create_task(
    subject="Implement feature",
    description="Add new functionality",
    active_form="Implementing feature"
)

# Update status
manager.update_task(task_id, status=TaskStatus.IN_PROGRESS)

# Get summary
summary = manager.get_summary()  # Formatted list with statuses
```

### 7. Display Utilities (`openagent/core/display.py`)

Claude Code style output formatting:

**Functions:**
- `display_tool_call_claude_style()` - Tool invocation display
- `display_tool_result_claude_style()` - Result display
- `display_write_result()` - Write operation with file info
- `display_edit_result()` - Edit with diff highlighting
- `format_diff_output()` - Unified diff formatting
- Color helpers: `bold()`, `green()`, `red()`, etc.

---

## Provider System

### BaseProvider ABC (`openagent/provider/base.py`)

Abstract base class defining the provider interface:

```python
class BaseProvider(ABC):
    def __init__(self, model: str, api_key: str | None = None)
    
    @abstractmethod
    async def chat(
        self,
        messages: list[Message],
        tools: list[ToolDef] | None = None,
        system_prompt: str = "",
    ) -> Message
    
    async def stream(...) -> AsyncIterator[str]
```

### Supported Providers

| Provider | Class | Model Examples |
|----------|-------|----------------|
| OpenAI | `OpenAIProvider` | gpt-4o, gpt-4-turbo, gpt-3.5-turbo |
| Anthropic | `AnthropicProvider` | claude-sonnet-4, claude-opus-4 |
| Google | `GoogleProvider` | gemini-2.0-flash, gemini-2.5-pro |
| Ollama | `OllamaProvider` | llama2, mistral, any local model |

### Provider Configuration

```python
# OpenAI
provider = OpenAIProvider(
    model="gpt-4o",
    api_key="sk-...",  # optional, uses OPENAI_API_KEY env var
    max_retries=3,
)

# Anthropic
provider = AnthropicProvider(
    model="claude-sonnet-4-20250514",
    api_key="sk-ant-...",
    max_tokens=4096,
)

# Ollama (local)
provider = OllamaProvider(
    model="llama2",
    base_url="http://localhost:11434",
)
```

---

## Tool System

### Tool Definition

Tools are defined using the `@tool` decorator:

```python
from openagent import tool

@tool
def get_weather(city: str) -> str:
    """Get the current weather for a city."""
    return f"The weather in {city} is sunny and 22°C."

# With custom name/description
@tool(name="search", description="Search the web")
def web_search(query: str, max_results: int = 5) -> str:
    # Implementation
    pass
```

### Built-in Tools Categories

#### File Operations
- `read(path, line_start, line_end)` - Read files with optional line range
- `write(path, content, create_parents)` - Create/overwrite files
- `edit(path, find, replace, expected_replacements)` - Find-and-replace edits
- `glob(pattern, path, max_results)` - File pattern search
- `grep(pattern, path, regex, context_lines)` - Content search
- `notebook_edit(path, cell_index, new_source, cell_type)` - Jupyter notebook editing

#### Shell Commands
- `bash(command, timeout, background, working_dir)` - Execute commands
- `bash_background(command, working_dir)` - Start persistent session
- `bash_output(session_id, tail_lines)` - Retrieve session output
- `kill_shell(session_id)` - Terminate session

#### Task Management
- `todo_write(tasks)` - Create multiple tasks
- `todo_update(task_id, status, subject, description)` - Update task
- `todo_list()` - Get formatted task list

#### Web & Search
- `web_search(query, num_results)` - DuckDuckGo search (requires duckduckgo-search)
- `web_fetch(url)` - Fetch webpage content (requires httpx)

#### Planning & Workflow
- `enter_plan_mode(reason)` - Enter planning mode
- `exit_plan_mode(approved_plan)` - Exit with approved plan
- `ask_user_question(question, options, multi_select)` - User input

#### Extensibility
- `skill(skill_name, args)` - Load specialized skills
- `slash_command(command, args)` - Execute custom commands
- `task(agent_type, description, context)` - Launch sub-agents

### Tool Security

All file/shell tools include security checks:
```python
project_root = os.environ.get('AGENT_PROJECT_ROOT')
if project_root and not str(file_path).startswith(os.path.realpath(project_root)):
    return f"Error: Access denied..."
```

---

## Data Flow

### Agent Execution Loop

```
┌─────────────┐
│ User Input  │
└──────┬──────┘
       ▼
┌─────────────┐     ┌──────────────┐
│ Add to      │────▶│ Session      │
│ Messages    │     │ History      │
└─────────────┘     └──────────────┘
       ▼
┌─────────────────────────────────────┐
│ Provider.chat(messages, tools)      │
│ → LLM generates response            │
└─────────────┬───────────────────────┘
              ▼
       Has Tool Calls?
         ┌───┴───┐
        No       Yes
         │        │
         ▼        ▼
    Return     Execute Tools
    Response   → Collect Results
                │
                ▼
           Add to Session
                │
                ▼
          Max Turns Reached?
         ┌───┴───┐
        No       Yes
         │        │
         └───────┘
```

### Tool Execution Flow

```
LLM Response with ToolCall
              │
              ▼
    ToolRegistry.execute()
              │
              ├─→ Lookup tool by name
              │   └─→ Not found? → Error result
              │
              ├─→ Extract arguments
              │
              ├─→ Check if async function
              │   ├─→ Yes: await func(**args)
              │   └─→ No:  func(**args)
              │
              ├─→ Convert result to string
              │   ├─→ Already str? → Keep
              │   └─→ Dict/list? → json.dumps()
              │
              └─→ Return ToolResultBlock
```

---

## Design Patterns

### 1. Provider Abstraction Pattern

All providers implement `BaseProvider` ABC, ensuring consistent interface:

```python
class BaseProvider(ABC):
    @abstractmethod
    async def chat(...) -> Message
    
    async def stream(...) -> AsyncIterator[str]
```

**Benefits:**
- Swappable providers without code changes
- Unified error handling
- Consistent streaming interface

### 2. Tool Registry Pattern

Centralized tool discovery and execution:

```python
class ToolRegistry:
    _tools: dict[str, ToolEntry]
    
    def register(func: Callable) -> None
    def get(name: str) -> Callable | None
    async def execute(tool_call: ToolUseBlock) -> ToolResultBlock
```

**Benefits:**
- Automatic schema generation
- Type-safe tool invocation
- Error isolation per tool

### 3. Session Persistence Pattern

Conversation state management with file I/O:

```python
class Session:
    messages: list[Message]
    
    def save(path: str) -> None
    @classmethod
    def load(path: str) -> Session
```

**Benefits:**
- Debugging via conversation replay
- Long-running task support
- State restoration after crashes

### 4. Decorator Pattern for Tools

Python decorators simplify tool definition:

```python
@tool
def my_tool(param: str) -> str:
    """Description."""
    pass

# Equivalent to:
my_tool = tool(my_tool)
# Sets metadata attributes automatically
```

---

## Integration Points

### MCP (Model Context Protocol) Integration

OpenAgent supports MCP for external tool discovery:

```python
from openagent import Agent, OpenAIProvider
from openagent.infrastructure import McpClient

async with McpClient("npx", ["@modelcontextprotocol/server-filesystem", "/path"]) as mcp:
    agent = Agent(
        provider=OpenAIProvider(model="gpt-4o"),
        mcp_client=mcp,  # Auto-discovers MCP tools
    )
```

**Transport Types:**
- **stdio**: Local subprocesses (e.g., `npx @mcp/server-filesystem`)
- **SSE**: Remote HTTP/SSE endpoints

### CoderAgent Specialization

`CoderAgent` lives in `openagent.apps.solocoder` and extends `Agent` with pre-configured tools for coding tasks:

```python
from openagent.apps.solocoder import create_coder

coder = await create_coder(model="gpt-4o")
result = await coder.run("Create a new Python file")
```

**Pre-loaded Tools:**
- All file operations (read, write, edit, glob, grep)
- Shell commands (bash, background execution)
- Task management (todo_write, todo_update, todo_list)
- Planning mode (enter_plan_mode, exit_plan_mode)
- Web tools (web_search, web_fetch)

### CLI Interface

Interactive command-line interface:

```bash
python -m openagent.coder --model gpt-4o --working-dir /project
```

**Quick Commands:**
- `@list` - List files in current directory
- `@todo` - Show task list
- `@clear` - Clear conversation history

---

## Error Handling & Retry Logic

### Exponential Backoff (`openagent/core/retry.py`)

Automatic retry on transient failures:

```python
# Configurable retry parameters
max_retries=3,
backoff_factor=2.0,
base_delay=1.0  # seconds
```

**Retryable Errors:**
- Network timeouts
- Rate limiting (429)
- Server errors (5xx)

### Tool Error Handling

Each tool execution is isolated:

```python
try:
    result = await func(**arguments)
except Exception as e:
    return ToolResultBlock(
        content=f"Error: {e}",
        is_error=True,
    )
```

---

## Logging & Debugging

### AgentLogger (`openagent/core/logging.py`)

Structured logging for agent operations:

```python
logger = AgentLogger(agent_id="my-agent")

logger.run_start(user_input)
logger.turn_start(turn_number, max_turns)
logger.turn_end(has_tools)
logger.run_end(final_turn)
```

### Configuration

```python
from openagent import configure_logging, logger

# Enable debug logging
configure_logging(level=logging.DEBUG)

# Direct access
logger.setLevel(logging.INFO)
logger.info("Custom message")
```

---

## Performance Considerations

### Async-First Design

All I/O operations are async:
- Provider chat calls
- Tool execution (when async)
- File I/O in future versions
- Network requests for web tools

### Streaming Support

Providers can stream token-by-token:

```python
async for chunk in provider.stream(messages):
    print(chunk, end="", flush=True)
```

**Benefits:**
- Lower perceived latency
- Progressive output display
- Better UX for long responses

---

## Security Considerations

### Path Validation

All file operations validate against `AGENT_PROJECT_ROOT`:

```python
project_root = os.environ.get('AGENT_PROJECT_ROOT')
if not str(file_path).startswith(os.path.realpath(project_root)):
    return "Error: Access denied..."
```

### Working Directory Isolation

Bash commands can be restricted to specific directories.

### Tool Result Sanitization

All tool results are converted to strings, preventing object injection.

---

## Future Roadmap

- [ ] Async file I/O (aiofiles integration)
- [ ] Image processing tools
- [ ] Database connectivity tools
- [ ] Enhanced MCP support (bidirectional)
- [ ] Multi-agent collaboration
- [ ] Web UI interface
- [ ] Agent templates and sharing

---

## References

- [Main README](../README.md) - User-facing documentation
- [API Reference](../api_reference.md) - Detailed API documentation
- [Contributing Guide](../CONTRIBUTING.md) - Development guidelines

---

*Document Version: 1.0*  
*Last Updated: 2025*
