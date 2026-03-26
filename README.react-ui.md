# SoloCoder Web UI

A modern React/TypeScript web interface for the SoloCoder Coder Agent, featuring real-time streaming, tool call visualization, and CLI-matching minimal style.

## Quick Start

```bash
# Backend (FastAPI)
cd backend
uv pip install -r requirements.txt

# Create .env file
cat > .env << EOF
OPENAI_API_KEY=your-api-key-here
FASTAPI_HOST=0.0.0.0
FASTAPI_PORT=8000
EOF

uv run uvicorn main:app --reload --host 0.0.0.0 --port 8000

# Frontend (React)
cd frontend
npm install
npm run dev
```

Frontend will be available at http://localhost:5173

## Features

- 💬 **Real-time chat** with streaming responses
- 🛠️ **Tool call visualization** with CLI-matching style
- 📊 **Status panel** with model selector and turn counter
- 🔌 **WebSocket support** for live updates
- 🎨 **Minimal design** matching SoloCoder CLI aesthetics
- 🔄 **Auto-reconnection** with exponential backoff
- ⚡ **Instant feedback** with optimistic UI updates

## Architecture

```
┌──────────────────────────────────────────────────┐
│              React + TypeScript Frontend          │
│  ┌────────────┬────────────┬───────────────────┐ │
│  │ Chat View  │ Status Panel│ Tool Call Display│ │
│  └────────────┴────────────┴───────────────────┘ │
└───────────────────┬──────────────────────────────┘
                    │ WebSocket / REST
┌───────────────────▼──────────────────────────────┐
│           FastAPI Backend (Python Async)          │
│  ┌────────────┬────────────┬───────────────────┐ │
│  │ API Routes │ WebSocket  │ Agent Service     │ │
│  └────────────┴────────────┴───────────────────┘ │
└───────────────────┬──────────────────────────────┘
                    │
┌───────────────────▼──────────────────────────────┐
│              SoloCoder / OpenAgent               │
│  ┌────────────┬────────────┬───────────────────┐ │
│  │ Coder Agent│ Tools      │ Session Storage   │ │
│  └────────────┴────────────┴───────────────────┘ │
└──────────────────────────────────────────────────┘
```

## API Endpoints

### REST

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/v1/` | Health check |
| `GET` | `/api/v1/models/list` | List available models |
| `POST` | `/api/v1/chat/message` | Send message, get response |
| `GET` | `/api/v1/session/status` | Get session status |
| `POST` | `/api/v1/session/reset` | Reset session |

### WebSocket

| Endpoint | Description |
|----------|-------------|
| `WS /api/v1/chat/stream` | Real-time message streaming |

## Environment Variables

### Backend (`.env`)

```bash
OPENAI_API_KEY=your-api-key-here
FASTAPI_HOST=0.0.0.0
FASTAPI_PORT=8000
```

## Project Structure

```
SoloCoder_react/
├── backend/                 # FastAPI backend
│   ├── api/                 # API routes and schemas
│   ├── services/           # Business logic (agent, streaming)
│   ├── src/                 # Actual implementation
│   └── tests/              # Test suite
├── frontend/               # React frontend
│   ├── src/
│   │   ├── components/    # UI components
│   │   │   ├── chat/     # Chat components
│   │   │   ├── status/   # Status panel
│   │   │   ├── tools/    # Tool display
│   │   │   └── ui/       # Reusable UI
│   │   ├── contexts/      # React context (AgentContext)
│   │   ├── hooks/         # Custom hooks
│   │   ├── lib/           # Utilities (API client, WebSocket)
│   │   └── types/         # TypeScript types
│   ├── public/
│   └── package.json
└── docs/                  # Documentation
```

## Development

### Backend

```bash
cd backend

# Run with hot reload
uv run uvicorn main:app --reload

# Run tests
uv run pytest

# Check linting
pylint backend/
```

### Frontend

```bash
cd frontend

# Start development server
npm run dev

# Build for production
npm run build

# Run type checking
npx tsc --noEmit

# Run linting
npm run lint
```

## VS Code Configuration

Create `.vscode/settings.json`:

```json
{
  "python.defaultInterpreterPath": "${workspaceFolder}/backend/.venv/bin/python",
  "python.testing.pytestEnabled": true,
  "python.testing.unittestEnabled": false,
  "editor.formatOnSave": true,
  "editor.defaultFormatter": "esbenp.prettier-vscode",
  "[typescript]": {
    "editor.defaultFormatter": "esbenp.prettier-vscode"
  },
  "[javascript]": {
    "editor.defaultFormatter": "esbenp.prettier-vscode"
  }
}
```

## Testing

### Backend Tests

```bash
cd backend
uv run pytest tests/ -v --asyncio-mode=auto
```

### Frontend Tests

```bash
cd frontend
npm test
```

## Deployment

### Docker Setup

Create `docker-compose.yml`:

```yaml
version: '3.8'

services:
  backend:
    build:
      context: ./backend
      dockerfile: Dockerfile
    ports:
      - "8000:8000"
    environment:
      - OPENAI_API_KEY=${OPENAI_API_KEY}
    restart: unless-stopped

  frontend:
    build:
      context: ./frontend
      dockerfile: Dockerfile
    ports:
      - "5173:80"
    depends_on:
      - backend
    restart: unless-stopped
```

### Build Images

```bash
docker-compose build
docker-compose up -d
```

## Troubleshooting

### Backend Issues

**"Module not found" errors in tests:**
```bash
cd backend
uv pip install -r requirements.txt
uv run pytest --asyncio-mode=auto
```

**API not responding:**
- Check if uvicorn is running: `uv run uvicorn main:app --reload`
- Verify CORS settings in `main.py`
- Check firewall settings
- Ensure port 8000 is not in use

### Frontend Issues

**Cannot connect to API:**
- Verify backend is running
- Check proxy configuration in `vite.config.ts`
- Inspect network tab in DevTools

**TypeScript errors:**
```bash
cd frontend
npx tsc --noEmit
```

## Contributing

1. Fork the repository
2. Create feature branch: `git checkout -b feature/amazing-feature`
3. Commit changes: `git commit -m 'Add amazing feature'`
4. Push to branch: `git push origin feature/amazing-feature`
5. Open Pull Request

## License

MIT License - see LICENSE file for details

## Acknowledgments

- Built on top of [SoloCoder](https://github.com/anthropics/openagent)
- Inspired by [Claude Code](https://www.anthropic.com/) design philosophy
- Powered by [FastAPI](https://fastapi.tiangolo.com/) and [Vite](https://vitejs.dev/)

---

**Happy coding! 🚀**