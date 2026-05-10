# SoloCoder

**A local CLI coding assistant powered by Qwen3.5-35B via LM Studio.**

[![Python](https://img.shields.io/badge/Python-3.11%2B-blue)](https://python.org)
[![License](https://img.shields.io/badge/License-MIT-green)](LICENSE)

---

## Overview

SoloCoder is a Claude Code-style CLI assistant that runs entirely locally on consumer hardware (RTX 5090). It uses **OpenAgent**, an async-first agent framework, with Qwen3.5-35B served through LM Studio—no cloud APIs required.

**Benefits:**
- **Privacy**: Your codebase never leaves your machine
- **Cost**: Zero per-token costs after hardware investment
- **Offline**: Works without internet connectivity
- **Ownership**: No vendor lock-in or usage quotas

---

## Architecture

```
┌─────────────────────────────────────┐
│    SoloCoder CLI (Interactive)      │
└──────────────┬──────────────────────┘
               │
               ▼
┌─────────────────────────────────────┐
│        OpenAgent Framework          │
│  Agent Core | Tools | Sessions      │
└──────────────┬──────────────────────┘
               │
               ▼
┌─────────────────────────────────────┐
│   LM Studio (Local API)             │
│    Qwen3.5-35B-A3B                  │
└─────────────────────────────────────┘
```

**Components:**
| Component | Purpose |
|-----------|---------|
| **OpenAgent** | Async-first agent framework with pluggable providers, tool registry, session persistence |
| **Coder Agent** | Specialized coding agent with file operations, shell execution, task tracking |
| **LM Studio** | Local LLM server providing OpenAI-compatible API |
| **Qwen3.5-35B-A3B** | Local model serving as the agent's "brain" |

---

## Features

### Agent Capabilities
- File operations (read, write, edit, glob, grep)
- Shell execution for testing/building
- Task tracking and breakdown
- Multi-turn conversations with context

### Interactive CLI
- Quick commands: `/list`, `/read`, `/todo`, `/model`, `/clear`
- Direct shell access via `! <command>`
- Turn counter for conversation budget

### Provider Support (OpenAgent)
- OpenAIProvider (GPT, LM Studio)
- AnthropicProvider (Claude)
- GoogleProvider (Gemini)
- OllamaProvider (local models)

---

## Setup

### Prerequisites
- Python 3.11+
- GPU with ~24GB VRAM (RTX 5090 recommended for 35B models)

### Installation

```bash
git clone https://github.com/your-repo/SoloCoder.git
cd SoloCoder
uv sync  # or: pip install -e ".[all]"
```

### LM Studio Setup

1. Download and install [LM Studio](https://lmstudio.ai/)
2. Pull `Qwen3.5-35B-A3B` (or similar variant)
3. Start server with context length ≥64K (typically `http://localhost:1234/v1`)

### Run

```bash
python cli_coder.py --model qwen3.5-35b-a3b \
    --base-url http://localhost:1234/v1 \
    -w /path/to/project
```

**Alternative with Ollama:**
```bash
ollama pull qwen2.5:32b
python cli_coder.py --model qwen2.5:32 --working-dir .
```

---

## Usage

### Interactive Session
```bash
python cli_coder.py -w . --base-url http://localhost:1234/v1
```

Then type requests like:
- "Create a new Python file with a hello world function"
- "Add error handling to the API endpoint in main.py"
- "Search for all TODO comments and create a task list"

### Quick Commands
| Command | Description |
|---------|-------------|
| `/list` or `/ls` | List files |
| `/read <file>` | Preview file contents |
| `/todo` | Show task list |
| `/model` | Change LLM model |
| `/clear` | Clear history |

### Direct Shell
Prefix with `!` for immediate execution:
```
! ls -la          # List files
! python main.py  # Run script
! git status      # Check git state
```

---

## Configuration

### Command Line Options
```bash
python cli_coder.py [OPTIONS]

--model, -m MODEL       LLM model name (default: gpt-4o)
--working-dir, -w DIR   Working directory for file operations
--max-turns, -t N       Max conversation turns (default: 20)
--api-key, -k KEY       API key (optional for local servers)
--base-url              OpenAI-compatible API URL
```

### Environment Variables
| Variable | Purpose |
|----------|---------|
| `OPENAI_API_KEY` | OpenAI provider |
| `ANTHROPIC_API_KEY` | Anthropic provider |
| `GOOGLE_API_KEY` | Google provider |

---

## Project Structure

```
SoloCoder/
├── cli_coder.py              # Main CLI entry point with interactive session
├── openagent/                # Agent framework package
│   ├── __init__.py           # Public API exports
│   ├── coder.py              # CoderAgent - specialized coding agent
│   ├── core/                 # Core components
│   │   ├── agent.py          # Agent class - main orchestrator
│   │   ├── types.py          # Canonical types (Message, ToolUseBlock, etc.)
│   │   ├── tool.py           # @tool decorator and registry
│   │   ├── session.py        # Session management and persistence
│   │   ├── logging.py        # Logging configuration
│   │   ├── retry.py          # Retry logic with exponential backoff
│   │   ├── display.py        # Output formatting and display
│   │   ├── bash_manager.py   # Shell command execution manager
│   │   ├── task_manager.py   # Task tracking and management
│   │   └── skill_manager.py  # Skill/command system
│   ├── provider/             # LLM providers
│   │   ├── base.py           # BaseProvider ABC
│   │   ├── converter.py      # Message conversion utilities
│   │   ├── openai.py         # OpenAI-compatible API support
│   │   ├── anthropic.py      # Anthropic/Claude support
│   │   ├── google.py         # Google/Gemini support
│   │   └── ollama.py         # Ollama local models support
│   ├── tools/                # Built-in tools
│   │   ├── __init__.py       # Tool exports
│   │   └── builtin.py        # File, shell, search tool implementations
│   └── mcp.py                # MCP client integration
├── tests/                    # Test suite
│   ├── test_agent.py         # Agent core tests
│   ├── test_builtin_tools.py # Built-in tools tests
│   ├── test_session.py       # Session persistence tests
│   └── ...                   # Additional test files
├── examples/                 # Usage examples
│   ├── coder_example.py      # CoderAgent usage example
│   ├── example.py            # Basic agent example
│   └── ...                   # More examples
├── dev_docs/                 # Development documentation
│   └── architecture.md       # Architecture details
├── CLAUDE.md                 # Project guidelines for Claude
└── README.md                 # This file
```

---

## License

MIT License — feel free to use and modify.
