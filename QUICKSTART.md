# Quick Start

## Backend Setup

1. **Install dependencies:**
```bash
cd /Users/taozeng/Projects/SoloCoder/../SoloCoder_react/backend
uv pip install -r requirements.txt
```

2. **Create `.env` file:**
```bash
cat > backend/.env << EOF
OPENAI_API_KEY=your-openai-api-key-here
FASTAPI_HOST=0.0.0.0
FASTAPI_PORT=8000
EOF
```

3. **Start the server:**
```bash
uv run uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

Backend will be available at http://localhost:8000

Test endpoint:
```bash
curl http://localhost:8000/api/v1/models/list
```

## Frontend Setup

1. **Install dependencies:**
```bash
cd /Users/taozeng/Projects/SoloCoder/../SoloCoder_react/frontend
npm install
```

2. **Start dev server:**
```bash
npm run dev
```

Frontend will be available at http://localhost:5173

## Usage

1. Open http://localhost:5173
2. API requests automatically proxy to backend at port 8000
3. Start chatting!

## Environment Variables

### Backend (Required)

| Variable | Description | Example |
|----------|-------------|---------|
| `OPENAI_API_KEY` | Your OpenAI API key | `sk-...` |
| `FASTAPI_HOST` | API host (default: 0.0.0.0) | `0.0.0.0` |
| `FASTAPI_PORT` | API port (default: 8000) | `8000` |

### Frontend

Frontend automatically proxies to backend through Vite. No additional config needed.

## API Endpoints

### Chat

- `GET /api/v1/chat/` - Health check
- `POST /api/v1/chat/message` - Send message

Models:
- `GET /api/v1/models/list` - List available models

Session:
- `GET /api/v1/session/status` - Session status
- `POST /api/v1/session/reset` - Reset session

### WebSocket

- `WS /api/v1/chat/stream` - Real-time streaming

## Troubleshooting

**"ModuleNotFoundError: No module named 'openagent'"**

Install from the main SoloCoder repo:
```bash
# From SoloCoder repo
cd /Users/taozeng/Projects/SoloCoder
uv pip install -e .

# Test
uv run python -c "import openagent"
```

**CORS errors**

Check `main.py` has CORS middleware configured with your frontend URL.

**Port already in use**

Change port in `.env`:
```
FASTAPI_PORT=8001
```

Update `vite.config.ts` proxy:
```typescript
proxy: {
  '/api': {
    target: 'http://localhost:8001',
    changeOrigin: true,
  },
}
```

## Next Steps

1. Add your OpenAI API key
2. Test the chat interface
3. Try different model configurations
4. Enable WebSocket streaming in frontend

For more details, see `docs/superpowers/specs/2026-03-24-solocoder-web-ui-arch-design.md`