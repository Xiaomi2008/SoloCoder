# SoloCoder: Local AI Coding Agent

**Build Claude Code-like coding agents with fully local LLMs on a single GPU.**

A capable autonomous coding agent running entirely locally—powered by Qwen3.5-35B-A3B via LM Studio, without relying on cloud-hosted flagship models.

[![Python](https://img.shields.io/badge/Python-3.11%2B-blue)](https://python.org)
[![License](https://img.shields.io/badge/License-MIT-green)](LICENSE)

---

## Quick Start

```bash
# 1. Clone the repository
git clone https://github.com/your-repo/SoloCoder.git
cd SoloCoder

# 2. Install dependencies (using uv recommended)
uv sync

# Or using pip
pip install -e ".[all]"

# 3. Set up LM Studio (see Setup section below)

# 4. Run the CLI agent
python cli_coder.py --model qwen3.5-35b-a3b --base-url http://localhost:1234/v1

# 5. Or run the Streamlit web UI
streamlit run server.py
```

---

## Overview

SoloCoder demonstrates that **powerful autonomous coding agents don't require cloud APIs**. By leveraging modern local LLM inference through LM Studio, you can run a fully functional coding assistant on a single consumer GPU—specifically optimized for machines like the RTX 5090.

The backbone model is **Qwen3.5-35B-A3B**, served locally via LM Studio's OpenAI-compatible API. This setup provides:

- **Full privacy**: Your code never leaves your machine
- **Complete ownership**: No vendor lock-in, no usage quotas
- **Cost control**: Zero per-token costs after hardware investment
- **Offline capability**: Work without internet connectivity
- **Customizable models**: Swap in any compatible local model as needed

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
# CLI version
python cli_coder.py --model qwen3.5-35b-a3b --base-url http://localhost:1234/v1 --working-dir /path/to/project

# Web UI version
streamlit run server.py
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

## Command Line Options

```bash
python cli_coder.py [OPTIONS]

Options:
  --model, -m MODEL       LLM model name (default: gpt-4o)
  --working-dir, -w DIR   Working directory for file operations
  --max-turns, -t N       Max conversation turns before stopping (default: 20)
  --api-key, -k KEY       API key (optional for local servers)
  --base-url              OpenAI-compatible API URL (e.g., http://localhost:1234/v1)
```

---

## Configuration

### Environment Variables

| Variable | Purpose |
|----------|---------|
| `OPENAI_API_KEY` | API key for OpenAI provider |
| `ANTHROPIC_API_KEY` | API key for Anthropic provider |
| `GOOGLE_API_KEY` | API key for Google provider |
| `AGENT_PROJECT_ROOT` | Security confinement root (set automatically) |

### Create `.env` from Example

```bash
cp .env.example .env
# Edit .env with your configuration
```

Note: The `.env.example` file is provided for reference and should not be committed. Create your own `.env` file with actual values.

---

## Web UI

SoloCoder includes a Streamlit-based web interface for a more interactive experience:

```bash
# Run the web UI
streamlit run server.py

# Or specify port
streamlit run server.py --server.port 8502
```

The web UI provides:
- Chat-based interface for agent interaction
- Model selection dropdown
- API key input
- Turn counter and session management
- Clean, modern UI with status indicators

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

## Features

### Agent Capabilities

- **File Operations**: Read, write, edit files seamlessly
- **Code Search**: Find patterns across your project with regex grep
- **Shell Execution**: Run bash commands for testing and building
- **Task Tracking**: Automatically break down complex tasks into manageable steps
- **Multi-turn Conversations**: Maintain context across extended interactions

### Built-in Tools (OpenAgent)

| Category | Tools |
|----------|-------|
| File Operations | `read`, `write`, `edit`, `glob`, `grep`, `notebook_edit` |
| Shell Management | `bash`, `bash_background`, `bash_output`, `kill_shell` |
| Task Manager | `todo_write`, `todo_update`, `todo_list` |
| Web & Search | `web_search`, `web_fetch` (requires optional deps) |
| Computer Use | `screenshot`, `click`, `type_text`, `key_combination` (macOS GUI automation, requires `computer-use` deps) |
| Planning | `enter_plan_mode`, `exit_plan_mode` |
| User Interaction | `ask_user_question` |

### Provider Support

OpenAgent supports multiple LLM providers, making it easy to switch between cloud and local:

- **OpenAIProvider**: GPT models (with custom base_url for LM Studio)
- **AnthropicProvider**: Claude models
- **GoogleProvider**: Gemini models
- **OllamaProvider**: Local Ollama instances

---

## Project Structure

```
SoloCoder/
├── cli_coder.py              # Main CLI entry point with interactive session
├── server.py                 # Streamlit web UI entry point
├── openagent/                # Agent framework package
│   ├── __init__.py          # Public API exports
│   ├── apps/                # Application packages (including SoloCoder)
│   ├── infrastructure/      # MCP and shell infrastructure helpers
│   ├── model/               # Canonical message and tool block exports
│   ├── runtime/             # Runtime agent, context, and event surfaces
│   ├── core/                # Legacy/internal framework components
│   │   ├── agent.py         # Agent class — main orchestrator
│   │   ├── types.py         # Canonical types (Message, ToolUseBlock, etc.)
│   │   ├── tool.py          # @tool decorator and registry
│   │   ├── session.py       # Session management and persistence
│   │   ├── logging.py       # Logging configuration
│   │   └── retry.py         # Retry logic with exponential backoff
│   ├── provider/            # Provider implementations and compatibility imports
│   ├── providers/           # Shared provider event types
│   │   ├── base.py          # BaseProvider ABC
│   │   ├── openai.py        # OpenAI-compatible API support
│   │   ├── anthropic.py     # Anthropic/Claude support
│   │   ├── google.py        # Google/Gemini support
│   │   └── ollama.py        # Ollama local models support
│   ├── tools/               # Built-in tool implementations
│   ├── coder.py             # SoloCoder compatibility shim
│   └── mcp.py               # MCP compatibility shim
├── tests/                    # Test suite
├── examples/                 # Usage examples
├── .env.example              # Environment variables template
├── .streamlit/              # Streamlit configuration
└── pyproject.toml           # Package configuration
```

---

## Future Work

### Planned Enhancements

- [ ] Model quantization presets: Pre-configured settings for different GPU VRAM capacities
- [ ] Multi-model support: Seamlessly switch between models based on task complexity
- [ ] GPU memory monitoring: Real-time VRAM usage display in CLI
- [ ] Batch inference: Process multiple requests more efficiently
- [ ] Fine-tuning pipeline: Custom fine-tuning for project-specific patterns
- [ ] RAG integration: Vector search over codebase for better context retrieval

### Community Contributions Welcome

SoloCoder is designed to be extensible. Consider contributing:

- New tool implementations
- Additional provider integrations
- UI improvements and themes
- Documentation enhancements
- Performance optimizations for specific GPU architectures

---

## Tests

```bash
# Run all tests
pytest

# Run specific test file
pytest tests/test_agent.py

# Run async tests
pytest tests/test_agent.py::test_agent_run
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

## Demo

[![SoloCoder Demo](https://img.youtube.com/vi/iTODVW8L22c/maxresdefault.jpg)](https://www.youtube.com/watch?v=iTODVW8L22c)

▶️ *Click the image above to watch the demo*

**Built with ❤️ for the local-first AI movement.**
