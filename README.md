# SoloCoder: A Claude Code-like CLI Coding Assistant Built Entirely with Local LLMs

**Local LLMs on a single GPU can perform coding jobs as well as many cloud-based models.**

This repository itself is proof: the entire CLI coding assistant was created solely by a local LLM (Qwen3.5-35B-A3B via LM Studio) running on a single RTX 5090 GPU—no cloud APIs required.

[![Python](https://img.shields.io/badge/Python-3.11%2B-blue)](https://python.org)
[![License](https://img.shields.io/badge/License-MIT-green)](LICENSE)

---

## Overview

**This project proves that local LLMs on a single GPU can perform coding tasks as well as many cloud-based models.**

The entire CLI coding assistant you see here was created solely by Qwen3.5-35B-A3B running locally via LM Studio on an RTX 5090—no cloud APIs, no external dependencies for the actual development work.

This setup provides:

- **Full privacy**: Your code never leaves your machine
- **Complete ownership**: No vendor lock-in, no usage quotas
- **Cost control**: Zero per-token costs after hardware investment
- **Offline capability**: Work without internet connectivity

---

## Why This Matters

### The Cloud Dependency Problem

Most AI coding assistants today rely on cloud-hosted models (GPT-4, Claude, etc.). While convenient, this approach has significant drawbacks:

| Issue | Cloud Models | Local SoloCoder |
|-------|--------------|-----------------|
| **Privacy** | Code sent to external servers | Entirely local execution |
| **Cost** | Per-token pricing accumulates | One-time hardware cost |
| **Latency** | Network round-trips required | Direct GPU inference |
| **Availability** | Dependent on internet/API uptime | Works offline, always available |
| **Customization** | Fixed model capabilities | Choose any compatible model |

### The Proof Is In the Code

This very repository was built entirely by Qwen3.5-35B-A3B running locally on a single RTX 5090 via LM Studio. No cloud APIs were used in its creation—the coding assistant itself wrote this codebase, demonstrating that local LLMs can handle real development work competently.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    SoloCoder CLI                            │
│  (Interactive coding agent with Claude Code-style UI)       │
└──────────────────────┬──────────────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────────────┐
│                     OpenAgent                               │
│         Lightweight async-first agent framework             │
│  ┌─────────────┬──────────────┬─────────────────────────┐   │
│  │ Agent Core  │  Tool System │    Session Management   │   │
│  └─────────────┴──────────────┴─────────────────────────┘   │
└──────────────────────┬──────────────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────────────┐
│              LM Studio (Local OpenAI API)                   │
│           Qwen3.5-35B-A3B served locally                    │
└─────────────────────────────────────────────────────────────┘
```

### Core Components

| Component | Purpose |
|-----------|---------|
| **OpenAgent** | Async-first agent framework with pluggable providers, tool registry, and session persistence |
| **Coder Agent** | Specialized agent for code editing tasks with file operations, shell execution, and task tracking |
| **LM Studio** | Local LLM server providing OpenAI-compatible API endpoint |
| **Qwen3.5-35B-A3B** | Backbone model serving as the "brain" of the coding agent |

### Workflow

1. User types a coding request in the CLI interface
2. Coder Agent processes the request using its tool system (file read/write, shell commands, grep search)
3. The OpenAgent framework formats messages and sends them to the local LM Studio endpoint
4. Qwen3.5-35B-A3B generates a response with tool calls or code changes
5. Results are displayed in Claude Code-style formatting

---

## Features

### Agent Capabilities

- **File Operations**: Read, write, edit files seamlessly
- **Code Search**: Find patterns across your project with regex grep
- **Shell Execution**: Run bash commands for testing and building
- **Task Tracking**: Automatically break down complex tasks into manageable steps
- **Multi-turn Conversations**: Maintain context across extended interactions

### Interactive CLI Features

- **Claude Code-style display**: Clean, readable output with syntax highlighting
- **Quick commands**: `/list`, `/read`, `/todo`, `/model`, `/clear` for common operations
- **Direct shell access**: `! <command>` prefix for immediate terminal execution
- **Turn tracking**: Visual indicator of remaining conversation budget

### Built-in Tools (OpenAgent)

| Category | Tools |
|----------|-------|
| File Operations | `read`, `write`, `edit`, `glob`, `grep`, `notebook_edit` |
| Shell Management | `bash`, `bash_background`, `bash_output`, `kill_shell` |
| Task Manager | `todo_write`, `todo_update`, `todo_list` |
| Web & Search | `web_search`, `web_fetch` (requires optional deps) |
| Planning | `enter_plan_mode`, `exit_plan_mode` |
| User Interaction | `ask_user_question` |

### Provider Support

OpenAgent supports multiple LLM providers, making it easy to switch between cloud and local:

- **OpenAIProvider**: GPT models (with custom base_url for LM Studio)
- **AnthropicProvider**: Claude models
- **GoogleProvider**: Gemini models
- **OllamaProvider**: Local Ollama instances

---

## Setup

### Prerequisites

- Python 3.11+
- An RTX 5090 (or comparable GPU with ~24GB VRAM) for running Qwen3.5-35B-A3B
- Git (for cloning the repository)

### Step 1: Clone and Install Dependencies

```bash
git clone https://github.com/your-repo/SoloCoder.git
cd SoloCoder

# Using uv (recommended)
uv sync

# Or using pip
pip install -e ".[all]"
```

### Step 2: Set Up LM Studio

1. **Download and install [LM Studio](https://lmstudio.ai/)**

2. **Pull the Qwen3.5-35B-A3B model**:
   - Open LM Studio
   - Go to the search tab (magnifying glass icon)
   - Search for `Qwen3.5-35B-A3B` or similar variant
   - Download a quantized version (Q4_K_M or Q5_K_M recommended for balance of speed/quality)

3. **Start the local server**:
   - Go to the server tab (power plug icon)
   - Select your downloaded model
   - Choose a GPU layer count (max out if you have VRAM, otherwise ~20-28 layers for 35B models on 24GB)
   - Click "Start Server"
   - Note the local URL (typically `http://localhost:1234/v1`)

### Step 3: Run SoloCoder with LM Studio

```bash
# Quick start with default settings pointing to LM Studio
python cli_coder.py --model qwen3.5-35b-a3b --base-url http://localhost:1234/v1

# Or set the model name you loaded in LM Studio exactly
python cli_coder.py -m your-model-name-here -k "" --base-url http://localhost:1234/v1
```

**Note**: When using local servers like LM Studio, API keys are typically not required. You can pass an empty string or omit the `--api-key` flag.

### Alternative: Using Ollama

If you prefer Ollama over LM Studio:

```bash
# Install Ollama from https://ollama.com
curl -fsSL https://ollama.com/install.sh | sh

# Pull a model (e.g., Qwen2.5-32B as an alternative)
ollama pull qwen2.5:32b

# Run SoloCoder with Ollama provider
python cli_coder.py --model qwen2.5:32 --working-dir /path/to/project
```

---

## Usage Examples

### Basic Interactive Session

```bash
cd /path/to/your/project
python cli_coder.py -w . --base-url http://localhost:1234/v1
```

Then interact naturally:

> "Create a new Python file with a hello world function"

> "Add error handling to the API endpoint in main.py"

> "Search for all TODO comments and create a task list"

### Quick Commands (in CLI)

| Command | Description |
|---------|-------------|
| `/list` or `/ls` | List files in current directory |
| `/read <file>` | Preview file contents before editing |
| `/todo` | Show task list |
| `/model` | Change LLM model mid-session |
| `/clear` | Clear conversation history |

### Direct Shell Commands

Prefix with `!` for immediate terminal execution:

```
! ls -la          # List files
! python main.py  # Run a script
! git status      # Check git state
```

---

## Configuration

### Command Line Options

```bash
python cli_coder.py [OPTIONS]

Options:
  --model, -m MODEL       LLM model name (default: gpt-4o)
  --working-dir, -w DIR   Working directory for file operations
  --max-turns, -t N       Max conversation turns before stopping (default: 20)
  --api-key, -k KEY       API key (optional for local servers)
  --base-url              OpenAI-compatible API URL (e.g., http://localhost:1234/v1)
```

### Environment Variables

| Variable | Purpose |
|----------|---------|
| `OPENAI_API_KEY` | API key for OpenAI provider |
| `ANTHROPIC_API_KEY` | API key for Anthropic provider |
| `GOOGLE_API_KEY` | API key for Google provider |
| `AGENT_PROJECT_ROOT` | Security confinement root (set automatically) |

---

## Limitations

### Hardware Requirements

Running 35B parameter models locally requires significant GPU resources:

| Model Size | Recommended VRAM | Minimum VRAM |
|------------|------------------|--------------|
| 7B | 8GB | 6GB |
| 14B | 12GB | 8GB |
| 35B | 24GB (RTX 5090) | 16GB (slower, more quantization) |

**Note**: Using lower VRAM will require heavier quantization, which may reduce model quality.

### Performance Considerations

- **Response time**: Local inference is slower than cloud APIs. Expect 2-10 seconds per token depending on GPU and model size
- **Context window**: Limited by available RAM when running large context windows
- **Concurrent tasks**: Running other GPU-intensive applications will impact performance

### Model Capabilities

While Qwen3.5-35B-A3B is highly capable, it may not match the raw capability of flagship cloud models (GPT-4o, Claude 3.5 Sonnet) on:
- Extremely complex reasoning tasks
- Very long context understanding (8K+ tokens)
- Specialized domain knowledge requiring massive training data

However, for most day-to-day coding tasks—reading files, writing functions, debugging errors, refactoring code—a well-tuned local 35B model provides excellent results.

---

## Future Work

### Planned Enhancements

- [ ] **Model quantization presets**: Pre-configured settings for different GPU VRAM capacities
- [ ] **Multi-model support**: Seamlessly switch between models based on task complexity
- [ ] **GPU memory monitoring**: Real-time VRAM usage display in CLI
- [ ] **Batch inference**: Process multiple requests more efficiently
- [ ] **Fine-tuning pipeline**: Custom fine-tuning for project-specific patterns
- [ ] **RAG integration**: Vector search over codebase for better context retrieval

### Community Contributions Welcome

SoloCoder is designed to be extensible. Consider contributing:

- New tool implementations
- Additional provider integrations
- UI improvements and themes
- Documentation enhancements
- Performance optimizations for specific GPU architectures

---

## Project Structure

```
SoloCoder/
├── cli_coder.py              # Main CLI entry point with interactive session
├── openagent/                # Agent framework package
│   ├── __init__.py          # Public API exports
│   ├── core/                # Core agent components
│   │   ├── agent.py         # Agent class — main orchestrator
│   │   ├── types.py         # Canonical types (Message, ToolUseBlock, etc.)
│   │   ├── tool.py          # @tool decorator and registry
│   │   ├── session.py       # Session management and persistence
│   │   ├── logging.py       # Logging configuration
│   │   └── retry.py         # Retry logic with exponential backoff
│   ├── provider/            # LLM provider implementations
│   │   ├── base.py          # BaseProvider ABC
│   │   ├── openai.py        # OpenAI-compatible API support
│   │   ├── anthropic.py     # Anthropic/Claude support
│   │   ├── google.py        # Google/Gemini support
│   │   └── ollama.py        # Ollama local models support
│   ├── tools/               # Built-in tool implementations
│   └── mcp.py               # MCP client integration
├── tests/                    # Test suite
└── examples/                 # Usage examples
```

---

## License

MIT License — feel free to use, modify, and distribute for personal or commercial projects.

---

## Acknowledgments

- **OpenAgent**: The underlying agent framework that powers SoloCoder's capabilities
- **LM Studio**: Excellent local LLM server with OpenAI-compatible API
- **Qwen Team**: For releasing high-quality open weights models
- **Claude Code**: Inspiration for the interactive CLI design and output formatting

---

**Built with ❤️ for the local-first AI movement.**
