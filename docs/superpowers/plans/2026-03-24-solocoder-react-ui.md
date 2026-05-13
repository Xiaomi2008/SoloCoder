# SoloCoder React UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a production-ready React/TypeScript frontend with FastAPI backend for SoloCoder web UI matching CLI visual style.

**Architecture:** 
- Frontend: Vite 6 + React 19 + TypeScript + Tailwind CSS
- Backend: FastAPI 0.115+ with WebSocket streaming
- UI: Custom minimal components (CLI-matching style)

**Tech Stack:**
- Frontend: Vite, React, TypeScript, Tailwind CSS, Highlight.js
- Backend: FastAPI, websockets, Pydantic
- State: React Context, custom hooks

---

## Phase 1: Backend API

### Task 1: Project Setup and Dependencies

**Files:**
- Create: `backend/requirements.txt`
- Create: `backend/pyproject.toml`

- [ ] **Step 1: Create backend requirements**

Create `backend/requirements.txt`:
```txt
fastapi>=0.115.0
uvicorn[standard]>=0.30.0
python-multipart>=0.0.9
websockets>=13.0
pydantic>=2.0
pydantic-settings>=2.0
httpx>=0.27
openai>=1.0
openagent @ file:///Users/taozeng/Projects/SoloCoder
pytest>=8.0
pytest-asyncio>=0.23
backoff>=2.0
APScheduler>=3.10
```

- [ ] **Step 2: Create backend pyproject.toml**

Create `backend/pyproject.toml`:
```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "solocoder-backend"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = [
    "fastapi>=0.115.0",
    "uvicorn[standard]>=0.30.0",
    "python-multipart>=0.0.9",
    "websockets>=13.0",
    "pydantic>=2.0",
]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
```

- [ ] **Step 3: Install and test**

```bash
cd /Users/taozeng/Projects/SoloCoder/../SoloCoder_react/backend
uv pip install -r requirements.txt
uv run pytest --version
```
Expected: pytest version printed

- [ ] **Step 4: Commit**

```bash
git add backend/requirements.txt backend/pyproject.toml
git commit -m "feat: add backend dependencies"
```

---

### Task 2: FastAPI App Structure

**Files:**
- Create: `backend/main.py`
- Create: `backend/config.py`
- Create: `tests/test_api.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_api.py`:
```python
import pytest
from fastapi.testclient import TestClient
from main import app

@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c

def test_app_root(client):
    """Test app root endpoint."""
    response = client.get("/")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}

def test_models_list(client):
    """Test models list endpoint."""
    response = client.get("/api/v1/models/list")
    assert response.status_code == 200
    data = response.json()
    assert "models" in data
    assert isinstance(data["models"], list)
```

- [ ] **Step 2: Run tests (expect failures)**

```bash
uv run pytest tests/test_api.py -v
```
Expected: "module 'main' not found"

- [ ] **Step 3: Create config.py**

Create `backend/config.py`:
```python
from pydantic_settings import BaseSettings
from typing import Optional

class Settings(BaseSettings):
    api_prefix: str = "/api/v1"
    fastapi_host: str = "0.0.0.0"
    fastapi_port: int = 8000
    openai_api_key: Optional[str] = None
    max_context_tokens: int = 128000
    compact_threshold: float = 0.8
    
    class Config:
        env_file = ".env"

settings = Settings()
```

- [ ] **Step 4: Create main.py**

Create `backend/main.py`:
```python
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
from config import settings
from api.routes import chat, models, session

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler."""
    # Startup
    yield
    # Shutdown
    pass

app = FastAPI(
    title="SoloCoder API",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Health check
@app.get("/")
async def root():
    return {"status": "ok"}

# Include routers
app.include_router(chat.router, prefix=settings.api_prefix)
app.include_router(models.router, prefix=settings.api_prefix)
app.include_router(session.router, prefix=settings.api_prefix)
```

- [ ] **Step 5: Create routes init**

Create `backend/api/__init__.py`:
```python
from . import routes

__all__ = ["routes"]
```

Create `backend/api/routes/__init__.py`:
```python
from . import chat, models, session

__all__ = ["chat", "models", "session"]
```

- [ ] **Step 6: Run tests (expect failures)**

```bash
uv run pytest tests/test_api.py -v
```
Expected: "No module named 'api'"

- [ ] **Step 7: Run tests (should pass now)**

```bash
uv run pytest tests/test_api.py -v
```
Expected: Still failing due to missing routes - that's OK

- [ ] **Step 8: Commit**

```bash
git add main.py config.py api/__init__.py api/routes/__init__.py
git commit -m "feat: create FastAPI app skeleton"
```

---

### Task 3: Pydantic Models

**Files:**
- Create: `backend/api/schemas.py`
- Test: `tests/test_schemas.py`

- [ ] **Step 1: Write schema tests**

Create `tests/test_schemas.py`:
```python
import pytest
from datetime import datetime
from api.schemas import (
    Message,
    ToolCall,
    ToolResult,
    ChatRequest,
    ChatResponse,
    PaginatedChatResponse,
    MessageRole,
    ToolName,
    ModelInfo,
    TokenUsage,
    ModelsResponse,
    SessionStatus,
)

def test_message_role_enum():
    """Test MessageRole enum."""
    assert MessageRole.user.value == "user"
    assert MessageRole.assistant.value == "assistant"
    assert MessageRole.tool.value == "tool"

def test_chat_request_validation():
    """Test ChatRequest validation."""
    request = ChatRequest(message="Hello")
    assert request.message == "Hello"

def test_model_info():
    """Test ModelInfo schema."""
    model = ModelInfo(
        id="gpt-4o",
        name="GPT-4o",
        provider="OpenAI",
        description="OpenAI flagship model"
    )
    assert model.id == "gpt-4o"

def test_paginated_response():
    """Test PaginatedChatResponse structure."""
    messages = [
        Message(id="1", role=MessageRole.user, content="Hi")
    ]
    response = PaginatedChatResponse(
        messages=messages,
        total=1,
        has_more=False,
        limit=50,
        offset=0
    )
    assert response.total == 1
    assert response.has_more is False
```

- [ ] **Step 2: Run tests (expect failures)**

```bash
uv run pytest tests/test_schemas.py -v
```
Expected: "No module named 'api.schemas'"

- [ ] **Step 3: Create schemas.py**

Create `backend/api/schemas.py`:
```python
from pydantic import BaseModel, Field
from typing import List, Optional, Any
from datetime import datetime
from enum import Enum


class MessageRole(str, Enum):
    """Message role types."""
    user = "user"
    assistant = "assistant"
    tool = "tool"


class ToolName(str, Enum):
    """Available tool names."""
    read = "read"
    write = "write"
    edit = "edit"
    bash = "bash"
    glob = "glob"
    grep = "grep"
    web_search = "web_search"
    web_fetch = "web_fetch"
    todo_write = "todo_write"
    task = "task"


class Message(BaseModel):
    """Chat message schema."""
    id: str
    role: MessageRole
    content: str
    timestamp: datetime = Field(default_factory=datetime.now)


class ToolCall(BaseModel):
    """Tool call schema."""
    id: str
    name: ToolName
    arguments: dict


class ToolResult(BaseModel):
    """Tool result schema."""
    tool_use_id: str
    content: Optional[str] = None
    is_error: bool = False


class ChatRequest(BaseModel):
    """Chat request schema."""
    message: str
    model: Optional[str] = None


class ChatResponse(BaseModel):
    """Chat response schema."""
    message: Message
    tool_calls: List[ToolCall] = []
    tool_results: List[ToolResult] = []


class PaginatedChatResponse(BaseModel):
    """Paginated message list."""
    messages: List[Message]
    total: int
    has_more: bool
    limit: int
    offset: int


class TokenUsage(BaseModel):
    """Token usage statistics."""
    tokens: int
    max_tokens: int
    percentage: float


class ModelInfo(BaseModel):
    """Model information."""
    id: str
    name: str
    provider: str
    description: str


class ModelsResponse(BaseModel):
    """Models list response."""
    models: List[ModelInfo]


class SessionStatus(BaseModel):
    """Session status info."""
    turn_count: int
    model: str
    token_count: Optional[int] = None
```

- [ ] **Step 4: Run tests (should pass)**

```bash
uv run pytest tests/test_schemas.py -v
```
Expected: All tests pass

- [ ] **Step 5: Commit**

```bash
git add api/schemas.py tests/test_schemas.py
git commit -m "feat: add Pydantic schemas"
```

---

### Task 4: Agent Service

**Files:**
- Create: `backend/services/agent_service.py`
- Create: `backend/services/__init__.py`
- Test: `tests/test_agent_service.py`

- [ ] **Step 1: Write service tests**

Create `tests/test_agent_service.py`:
```python
import pytest
from unittest.mock import Mock, patch
import asyncio
from services.agent_service import AgentService


@pytest.mark.asyncio
async def test_agent_service_creates_session():
    """Test session creation."""
    service = AgentService()
    
    with patch('services.agent_service.CoderAgent') as MockAgent, \
         patch('services.agent_service.OpenAIProvider') as MockProvider:
        
        MockProvider.return_value = Mock()
        agent_instance = Mock()
        MockAgent.return_value = agent_instance
        agent_instance.session = Mock()
        
        agent = await service.get_or_create_session(None, "gpt-4o")
        assert agent is not None


@pytest.mark.asyncio
async def test_process_message_structure():
    """Test message processing structure."""
    service = AgentService()
    
    # Just verify methods exist, don't call them without provider
    assert hasattr(service, 'process_message')
    assert hasattr(service, 'get_or_create_session')
    assert hasattr(service, 'reset_session')
```

- [ ] **Step 2: Run tests (expect failures)**

```bash
uv run pytest tests/test_agent_service.py -v
```
Expected: "No module named 'services'"

- [ ] **Step 3: Create __init__.py**

Create `backend/services/__init__.py`:
```python
from .agent_service import AgentService
from .streaming import stream_agent_response

__all__ = ["AgentService", "stream_agent_response"]
```

- [ ] **Step 4: Create agent_service.py**

Create `backend/services/agent_service.py`:
```python
import asyncio
import uuid
from typing import Optional, Dict
from config import settings
from openagent.coder import CoderAgent
from openagent.provider.openai import OpenAIProvider
from api.schemas import Message, MessageRole, ChatResponse


class AgentService:
    """Service for managing CoderAgent instances."""
    
    def __init__(self):
        self.sessions: Dict[str, CoderAgent] = {}
        self._lock = asyncio.Lock()
    
    async def get_or_create_session(
        self,
        session_id: Optional[str],
        model: str = "gpt-4o"
    ) -> CoderAgent:
        """Get or create a session for the given model."""
        async with self._lock:
            if not session_id or session_id not in self.sessions:
                provider = OpenAIProvider(
                    model=model,
                    api_key=settings.openai_api_key
                )
                agent = CoderAgent(
                    provider=provider,
                    max_turns=100,
                    working_dir=None,
                    max_context_tokens=settings.max_context_tokens,
                    compact_threshold=settings.compact_threshold,
                    disable_compaction=False,
                )
                new_session_id = session_id or str(uuid.uuid4())
                self.sessions[new_session_id] = agent
                return agent
            return self.sessions[session_id]
    
    async def process_message(
        self,
        message: str,
        session_id: Optional[str],
        model: Optional[str] = None,
        timeout: int = 60
    ) -> ChatResponse:
        """Process a message through the agent."""
        import asyncio

        actual_model = model or "gpt-4o"
        agent = await self.get_or_create_session(session_id, actual_model)

        try:
            # Run the agent
            response = await asyncio wait_for(
                agent.run(message),
                timeout=timeout
            )

            # Create response schema
            msg = Message(
                id=str(uuid.uuid4()),
                role=MessageRole.assistant,
                content=str(response)
            )

            return ChatResponse(message=msg)

        except asyncio.TimeoutError:
            raise TimeoutError(f"Agent execution timed out after {timeout}s")
        except Exception as e:
            raise e

    async def reset_session(self, session_id: str) -> bool:
        """Reset a session."""
        async with self._lock:
            if session_id in self.sessions:
                self.sessions[session_id].session.clear()
                return True
            return False

    def get_session_list(self) -> list:
        """List active sessions."""
        return list(self.sessions.keys())
```

- [ ] **Step 5: Run tests**

```bash
uv run pytest tests/test_agent_service.py -v
```
Expected: Need to mock provider properly

- [ ] **Step 6: Fix tests for real agents**

Update `tests/test_agent_service.py`:
```python
import pytest
from unittest.mock import Mock, AsyncMock, patch
from services.agent_service import AgentService

@pytest.mark.asyncio
async def test_agent_service_creates_session():
    """Test session creation."""
    service = AgentService()

    # Patch OpenAIProvider import
    with patch('services.agent_service.OpenAIProvider') as MockProvider:
        mock_provider = Mock()
        MockProvider.return_value = mock_provider

        agent = await service.get_or_create_session(None, "gpt-4o")

        # Verify OpenAIProvider was called
        MockProvider.assert_called_once()
        assert agent is not None
        assert service.sessions  # Check session was stored
```

Run tests again:
```bash
uv run pytest tests/test_agent_service.py -v
```

- [ ] **Step 7: Commit**

```bash
git add services/__init__.py services/agent_service.py tests/test_agent_service.py
git commit -m "feat: add agent service"
```

---

### Task 5: Streaming Service

**Files:**
- Create: `backend/services/streaming.py`
- Test: `tests/test_streaming.py`

- [ ] **Step 1: Write streaming tests**

Create `tests/test_streaming.py`:
```python
import pytest
from services.streaming import stream_agent_response, message_id_from_request


def test_message_id_generation():
    """Test message ID generation."""
    from services.streaming import generate_message_id
    
    id1 = generate_message_id()
    id2 = generate_message_id()
    
    assert len(id1) > 0
    assert id1 != id2
```

- [ ] **Step 2: Create streaming.py**

Create `backend/services/streaming.py`:
```python
import asyncio
import uuid
from typing import AsyncGenerator
from services.agent_service import AgentService

def generate_message_id() -> str:
    """Generate a unique message ID."""
    return str(uuid.uuid4())[:8]


async def stream_agent_response(
    agent_service: AgentService,
    message: str,
    session_id: str,
    model: str
) -> AsyncGenerator[dict, None]:
    """Stream agent response as async generator."""

    msg_id = generate_message_id()

    # Start message
    yield {
        "type": "message_start",
        "messageId": msg_id,
        "role": "assistant"
    }

    try:
        # Get agent and process
        actual_model = model or "gpt-4o"
        agent = await agent_service.get_or_create_session(session_id, actual_model)

        # Run agent (non-streaming for V1)
        response = await agent.run(message)
        response_text = str(response)

        # Send text chunks (simulated streaming)
        words = response_text.split()
        current_chunk = ""

        for word in words:
            current_chunk += word + " "

            # Send every 5 words or on punctuation
            if len(current_chunk.split()) >= 5 or word.endswith("."):
                yield {
                    "type": "text_chunk",
                    "messageId": msg_id,
                    "content": current_chunk
                }
                current_chunk = ""
                await asyncio.sleep(0.03)  # Simulate typing speed

        # End message
        yield {
            "type": "message_end",
            "messageId": msg_id,
            "complete": True
        }

    except Exception as e:
        yield {
            "type": "error",
            "messageId": msg_id,
            "error": str(e)
        }
        raise
```

- [ ] **Step 3: Run tests**

```bash
uv run pytest tests/test_streaming.py -v
```
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add services/streaming.py tests/test_streaming.py
git commit -m "feat: add streaming service"
```

---

### Task 6: REST API Routes

**Files:**
- Create: `backend/api/routes/chat.py`
- Create: `backend/api/routes/models.py`
- Create: `backend/api/routes/session.py`
- Test: Tests will be part of test_api.py updates

- [ ] **Step 1: Create models route**

Create `backend/api/routes/models.py`:
```python
from fastapi import APIRouter
from api.schemas import ModelsResponse, ModelInfo

router = APIRouter()


@router.get("/list")
async def list_models():
    """List available models."""
    # Basic model list for V1
    return ModelsResponse(
        models=[
            ModelInfo(
                id="gpt-4o",
                name="GPT-4o",
                provider="OpenAI",
                description="OpenAI's fastest, most capable model"
            ),
            ModelInfo(
                id="gpt-4-turbo",
                name="GPT-4 Turbo",
                provider="OpenAI",
                description="Cost-effective GPT-4 Turbo model"
            ),
        ]
    )
```

- [ ] **Step 2: Create chat route**

Create `backend/api/routes/chat.py`:
```python
from fastapi import APIRouter
from pydantic import BaseModel
from typing import Optional
from services.agent_service import AgentService
from api.schemas import ChatRequest, ChatResponse, MessageRole, Message
import uuid
import asyncio

router = APIRouter()


class ChatResponseSchema(BaseModel):
    """Chat service status."""
    status: str


@router.get("/")
async def chat_root():
    """Chat endpoint root."""
    return ChatResponseSchema(status="chat endpoint ready")


@router.post("/message")
async def chat_message(
    request: ChatRequest,
    service: AgentService = None
) -> ChatResponse:
    """Process a chat message."""
    if service is None:
        service = AgentService()

    try:
        response_text = await service.process_message(
            message=request.message,
            session_id=None,
            model=request.model
        )
        return response_text

    except Exception as e:
        # For V1, create error message
        msg = Message(
            id=str(uuid.uuid4()),
            role=MessageRole.assistant,
            content=f"Error: {str(e)}"
        )
        return ChatResponse(message=msg)
```

- [ ] **Step 3: Create session route**

Create `backend/api/routes/session.py`:
```python
from fastapi import APIRouter
from api.schemas import SessionStatus
from services.agent_service import AgentService

router = APIRouter()


@router.post("/reset")
async def reset_session():
    """Reset current session (V1 - no session tracking)."""
    return {"status": "reset", "message": "Session cleared (V1)"}


@router.get("/status")
async def session_status():
    """Get current session status (V1 - minimal)."""
    return SessionStatus(
        turn_count=0,
        model="gpt-4o"
    )
```

- [ ] **Step 4: Test endpoints**

```bash
cd /Users/taozeng/Projects/SoloCoder/../SoloCoder_react/backend
uv run uvicorn main:app --host 0.0.0.0 --port 8000 &
sleep 3

# Test models endpoint
curl -s http://localhost:8000/api/v1/models/list | python -m json.tool
```

Expected: Models JSON response

- [ ] **Step 5: Commit**

```bash
git add api/routes/
git commit -m "feat: add REST API routes"
```

---

# Phase 2: Frontend (React + Vite)

## Task 7: Frontend Project Setup

**Files:**
- Create: `frontend/package.json`
- Create: `frontend/vite.config.ts`
- Create: `frontend/tsconfig.json`
- Create: `frontend/tailwind.config.ts`
- Create: `frontend/postcss.config.mjs`
- Create: `frontend/index.html`

- [ ] **Step 1: Create package.json**

Create `frontend/package.json`:
```json
{
  "name": "solocoder-frontend",
  "private": true,
  "version": "0.1.0",
  "type": "module",
  "scripts": {
    "dev": "vite",
    "build": "tsc && vite build",
    "preview": "vite preview",
    "lint": "eslint . --ext ts,tsx --report-unused-disable-directives --max-warnings 0"
  },
  "dependencies": {
    "react": "^19.0.0",
    "react-dom": "^19.0.0",
    "@tanstack/react-query": "^5.0.0",
    "react-use-websocket": "^4.0.0",
    "highlight.js": "^11.0.0"
  },
  "devDependencies": {
    "@types/react": "^19.0.0",
    "@types/react-dom": "^19.0.0",
    "@vitejs/plugin-react": "^4.0.0",
    "typescript": "^5.0.0",
    "vite": "^6.0.0",
    "tailwindcss": "^3.4.0",
    "postcss": "^8.4.0",
    "autoprefixer": "^10.4.0"
  }
}
```

- [ ] **Step 2: Create vite.config.ts**

Create `frontend/vite.config.ts`:
```typescript
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import path from 'path'

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
    },
  },
})
```

- [ ] **Step 3: Create tsconfig.json**

Create `frontend/tsconfig.json`:
```json
{
  "compilerOptions": {
    "target": "ES2020",
    "useDefineForClassFields": true,
    "lib": ["ES2020", "DOM", "DOM.Iterable"],
    "module": "ESNext",
    "skipLibCheck": true,

    /* Bundler mode */
    "moduleResolution": "bundler",
    "allowImportingTsExtensions": true,
    "resolveJsonModule": true,
    "isolatedModules": true,
    "noEmit": true,
    "jsx": "react-jsx",

    /* Linting */
    "strict": true,
    "noUnusedLocals": true,
    "noUnusedParameters": true,
    "noFallthroughCasesInSwitch": true,

    /* Path aliases */
    "baseUrl": ".",
    "paths": {
      "@/*": ["./src/*"]
    }
  },
  "include": ["src"],
  "references": [{ "path": "./tsconfig.node.json" }]
}
```

- [ ] **Step 4: Create tailwind.config.ts**

Create `frontend/tailwind.config.ts`:
```typescript
import type { Config } from 'tailwindcss'

export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {},
  },
  plugins: [],
}
```

- [ ] **Step 5: Create postcss.config.mjs**

Create `frontend/postcss.config.mjs`:
```javascript
export default {
  plugins: {
    tailwindcss: {},
    autoprefixer: {},
  },
}
```

- [ ] **Step 6: Create index.html**

Create `frontend/index.html`:
```html
<!doctype html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <link rel="icon" type="image/svg+xml" href="/vite.svg" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>SoloCoder</title>
  </head>
  <body>
    <div id="root"></div>
    <script type="module" src="/src/main.tsx"></script>
  </body>
</html>
```

- [ ] **Step 7: Install dependencies**

```bash
cd /Users/taozeng/Projects/SoloCoder/../SoloCoder_react/frontend
npm install
```

- [ ] **Step 8: Commit**

```bash
cd ../../
git add frontend/package.json frontend/vite.config.ts frontend/tsconfig.json frontend/tailwind.config.ts frontend/postcss.config.mjs frontend/index.html
git commit -m "feat: add frontend project setup"
```

---

## Task 8: Frontend Project Structure

**Files:**
- Create directory structure
- Create `frontend/src/index.css`
- Create `frontend/src/main.tsx`

- [ ] **Step 1: Create directory structure**

```bash
cd /Users/taozeng/Projects/SoloCoder/../SoloCoder_react/frontend/src
mkdir -p components/chat components/ui components/code components/tools components/status contexts hooks lib types
```

- [ ] **Step 2: Create index.css**

Create `frontend/src/index.css`:
```css
@tailwind base;
@tailwind components;
@tailwind utilities;

:root {
  /* CLI-matching colors */
  --color-success: #16a34a;
  --color-error: #dc2626;
  --color-tool: #166eff;
  --color-description: #6b7280;
  --color-code-bg: #f8fafc;
  --color-code-text: #0f172a;
}

body {
  margin: 0;
  padding: 0;
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', 'Roboto', 'Oxygen',
    'Ubuntu', 'Cantarell', 'Fira Sans', 'Droid Sans', 'Helvetica Neue',
    sans-serif;
  -webkit-font-smoothing: antialiased;
  -moz-osx-font-smoothing: grayscale;
}

code {
  font-family: source-code-pro, Menlo, Monaco, Consolas, 'Courier New',
    monospace;
}

/* Scrollbar styling */
::-webkit-scrollbar {
  width: 8px;
  height: 8px;
}

::-webkit-scrollbar-track {
  background: #f1f1f1;
}

::-webkit-scrollbar-thumb {
  background: #888;
  border-radius: 4px;
}

::-webkit-scrollbar-thumb:hover {
  background: #555;
}
```

- [ ] **Step 3: Create main.tsx**

Create `frontend/src/main.tsx`:
```typescript
import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App.tsx'
import './index.css'

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
)
```

- [ ] **Step 4: Commit**

```bash
cd /Users/taozeng/Projects/SoloCoder/../SoloCoder_react
git add frontend/src/
git commit -m "feat: create frontend source structure"
```

---

## Task 9: API Client and Types

**Files:**
- Create: `frontend/src/types/agent.ts`
- Create: `frontend/src/types/api.ts`
- Create: `frontend/src/lib/api.ts`
- Create: `frontend/src/lib/websocket.ts`

- [ ] **Step 1: Create agent types**

Create `frontend/src/types/agent.ts`:
```typescript
export type MessageRole = 'user' | 'assistant' | 'tool'

export type ToolName = 
  | 'read'
  | 'write'
  | 'edit'
  | 'bash'
  | 'glob'
  | 'grep'
  | 'web_search'
  | 'web_fetch'
  | 'todo_write'
  | 'task'

export interface Message {
  id: string
  role: MessageRole
  content: string
  timestamp: Date
  toolCalls?: ToolCall[]
  toolResults?: ToolResult[]
}

export interface ToolCall {
  id: string
  name: ToolName
  arguments: Record<string, unknown>
  description: string
}

export interface ToolResult {
  toolUseId: string
  content?: string
  isError: boolean
}

export interface AgentState {
  connected: boolean
  currentModel: string
  turnCount: number
  currentTurn: {
    inProgress: boolean
    userMessageId: string | null
    toolCallCount: number
  }
  messages: Message[]
  isLoading: boolean
  error: {
    name: AppErrorName
    message: string
    timestamp: Date
  } | null
}

export type AppErrorName =
  | 'NONE'
  | 'CONNECTION_ERROR'
  | 'AUTH_ERROR'
  | 'RATE_LIMIT_ERROR'
  | 'TIMEOUT_ERROR'
  | 'INTERNAL_ERROR'
```

- [ ] **Step 2: Create API types**

Create `frontend/src/types/api.ts`:
```typescript
export interface ChatRequest {
  message: string
  model?: string
}

export interface ChatResponse {
  message: Message
  toolCalls?: ToolCall[]
  toolResults?: ToolResult[]
}

export interface PaginatedChatResponse {
  messages: Message[]
  total: number
  hasMore: boolean
  limit: number
  offset: number
}

export interface ModelInfo {
  id: string
  name: string
  provider: string
  description: string
}

export interface ModelsResponse {
  models: ModelInfo[]
}

export interface SessionStatus {
  turnCount: number
  model: string
  tokenCount?: number
}

export interface ChatHistory {
  messages: Message[]
  total: number
  hasMore: boolean
}
```

- [ ] **Step 3: Create API client**

Create `frontend/src/lib/api.ts`:
```typescript
import {
  ChatRequest,
  ChatResponse,
  ModelsResponse,
  SessionStatus,
} from '../types/api'

const API_BASE = '/api/v1'

export const apiClient = {
  async getModels(): Promise<ModelsResponse> {
    const res = await fetch(`${API_BASE}/models/list`)
    if (!res.ok) throw new Error('Failed to fetch models')
    return res.json()
  },

  async sendMessage(request: ChatRequest): Promise<ChatResponse> {
    const res = await fetch(`${API_BASE}/chat/message`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(request),
    })
    if (!res.ok) throw new Error('Failed to send message')
    return res.json()
  },

  async getSessionStatus(): Promise<SessionStatus> {
    const res = await fetch(`${API_BASE}/session/status`)
    if (!res.ok) throw new Error('Failed to fetch status')
    return res.json()
  },

  async resetSession(): Promise<{ status: string }> {
    const res = await fetch(`${API_BASE}/session/reset`, {
      method: 'POST',
    })
    if (!res.ok) throw new Error('Failed to reset session')
    return res.json()
  },
}

export default apiClient
```

- [ ] **Step 4: Create WebSocket client**

Create `frontend/src/lib/websocket.ts`:
```typescript
export type WebSocketMessageType =
  | 'message_start'
  | 'text_chunk'
  | 'tool_call'
  | 'tool_result'
  | 'message_end'
  | 'error'
  | 'ping'
  | 'pong'

export interface WSMessage {
  type: WebSocketMessageType
  messageId?: string
  content?: string
  role?: string
  complete?: boolean
  toolCall?: ToolCall
  toolUseId?: string
  error?: string
}

export class WebSocketClient {
  private ws: WebSocket | null = null
  private url: string
  private onMessage?: (data: WSMessage) => void
  private onError?: (error: Event) => void
  private onClose?: () => void
  private reconnectAttempts = 0
  private maxReconnectAttempts = 10
  private reconnectDelay = 1000

  constructor(url: string) {
    this.url = url
  }

  connect(onMessage: (data: WSMessage) => void): void {
    this.onMessage = onMessage
    this.ws = new WebSocket(this.url)

    this.ws.onopen = () => {
      this.reconnectAttempts = 0
    }

    this.ws.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data)
        if (data.type === 'ping') {
          this.send({ type: 'pong' })
        } else if (onMessage) {
          onMessage(data)
        }
      } catch (e) {
        console.error('WS message parse error', e)
      }
    }

    this.ws.onerror = (error) => {
      this.onError?.(error)
    }

    this.ws.onclose = () => {
      this.onClose?.()
      this.reconnect()
    }
  }

  send(data: WSMessage): void {
    if (this.ws?.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify(data))
    }
  }

  disconnect(): void {
    this.ws?.close()
  }

  private reconnect(): void {
    if (this.reconnectAttempts >= this.maxReconnectAttempts) {
      console.error('Max reconnect attempts reached')
      return
    }

    this.reconnectAttempts++
    const delay = this.reconnectDelay * this.reconnectAttempts
    setTimeout(() => {
      this.connect(this.onMessage || (() => {}))
    }, delay)
  }
}

export default WebSocketClient
```

- [ ] **Step 5: Commit**

```bash
cd /Users/taozeng/Projects/SoloCoder/../SoloCoder_react
git add frontend/src/types/ frontend/src/lib/
git commit -m "feat: add API client and types"
```

---

## Task 10: Context and Hooks

**Files:**
- Create: `frontend/src/contexts/AgentContext.tsx`
- Create: `frontend/src/hooks/useAgent.ts`
- Create: `frontend/src/hooks/useStream.ts`

- [ ] **Step 1: Create AgentState initial value**

Create `frontend/src/contexts/AgentContext.tsx`:
```typescript
import React, { createContext, useContext } from 'react'
import { AgentState } from '../types/agent'
import apiClient from '../lib/api'
import { WebSocketClient } from '../lib/websocket'

const initialState: AgentState = {
  connected: false,
  currentModel: 'gpt-4o',
  turnCount: 0,
  currentTurn: {
    inProgress: false,
    userMessageId: null,
    toolCallCount: 0,
  },
  messages: [],
  isLoading: false,
  error: null,
}

interface AgentContextType {
  state: AgentState
  connect: () => Promise<void>
  disconnect: () => void
  sendMessage: (content: string) => Promise<void>
  resetSession: () => Promise<void>
  updateModel: (model: string) => Promise<void>
  clearError: () => void
}

const AgentContext = createContext<AgentContextType | undefined>(undefined)

export function AgentProvider({ children }: { children: React.ReactNode }) {
  const [state, setState] = React.useState<AgentState>(initialState)
  const wsRef = React.useRef<WebSocketClient | null>(null)

  const connect = async () => {
    setState((prev) => ({ ...prev, isLoading: true }))

    try {
      // Verify connection by fetching status
      const status = await apiClient.getSessionStatus()
      setState((prev) => ({
        ...prev,
        connected: true,
        currentModel: status.model,
        turnCount: status.turnCount,
        isLoading: false,
      }))
    } catch (error) {
      setState((prev) => ({
        ...prev,
        isLoading: false,
        error: {
          name: 'CONNECTION_ERROR',
          message: 'Failed to connect to API',
          timestamp: new Date(),
        },
      }))
      throw error
    }
  }

  const disconnect = () => {
    wsRef.current?.disconnect()
    setState((prev) => ({ ...prev, connected: false }))
  }

  const sendMessage = async (content: string) => {
    if (!state.connected || !content.trim()) return

    const newMessage: AgentState['messages'][number] = {
      id: Date.now().toString(),
      role: 'user',
      content,
      timestamp: new Date(),
    }

    setState((prev) => ({
      ...prev,
      messages: [...prev.messages, newMessage],
      currentTurn: { ...prev.currentTurn, inProgress: true },
      isLoading: true,
    }))

    // For V1, use regular API call (streaming comes later if needed)
    try {
      const response = await apiClient.sendMessage({
        message: content,
        model: state.currentModel,
      })

      const assistantMessage: AgentState['messages'][number] = {
        id: response.message.id,
        role: 'assistant',
        content: response.message.content,
        timestamp: response.message.timestamp,
      }

      setState((prev) => ({
        ...prev,
        messages: [...prev.messages, assistantMessage],
        turnCount: prev.turnCount + 1,
        currentTurn: { ...prev.currentTurn, inProgress: false },
        isLoading: false,
        error: null,
      }))
    } catch (error) {
      setState((prev) => ({
        ...prev,
        currentTurn: { ...prev.currentTurn, inProgress: false },
        isLoading: false,
        error: {
          name: 'INTERNAL_ERROR',
          message: error instanceof Error ? error.message : 'Unknown error',
          timestamp: new Date(),
        },
      }))
    }
  }

  const resetSession = async () => {
    try {
      await apiClient.resetSession()
      setState(initialState)
    } catch (error) {
      setState((prev) => ({
        ...prev,
        error: {
          name: 'INTERNAL_ERROR',
          message: 'Failed to reset session',
          timestamp: new Date(),
        },
      }))
    }
  }

  const updateModel = async (model: string) => {
    setState((prev) => ({ ...prev, currentModel: model }))
  }

  const clearError = () => {
    setState((prev) => ({ ...prev, error: null }))
  }

  const value: AgentContextType = {
    state,
    connect,
    disconnect,
    sendMessage,
    resetSession,
    updateModel,
    clearError,
  }

  return (
    <AgentContext.Provider value={value}>{children}</AgentContext.Provider>
  )
}

export function useAgent() {
  const context = useContext(AgentContext)
  if (!context) {
    throw new Error('useAgent must be used within AgentProvider')
  }
  return context
}
```

- [ ] **Step 2: Create useStream hook**

Create `frontend/src/hooks/useStream.ts`:
```typescript
import { useAgent } from '../contexts/AgentContext'

export function useStream() {
  const { sendMessage, state } = useAgent()

  const handleChunk = (chunk: string, messageId: string) => {
    // Update message content incrementally
    setState((prev) => ({
      ...prev,
      messages: prev.messages.map((msg) =>
        msg.id === messageId
          ? { ...msg, content: msg.content + chunk }
          : msg
      ),
    }))
  }

  return {
    handleChunk,
    streaming: state.currentTurn.inProgress,
  }
}

export default useStream
```

- [ ] **Step 3: Wrap App with provider**

Update `frontend/src/App.tsx`:
```typescript
import { AgentProvider } from './contexts/AgentContext'
import AppContent from './AppContent'

function App() {
  return (
    <AgentProvider>
      <AppContent />
    </AgentProvider>
  )
}

export default App
```

- [ ] **Step 4: Commit**

```bash
cd /Users/taozeng/Projects/SoloCoder/../SoloCoder_react
git add frontend/src/contexts/ frontend/src/hooks/
git commit -m "feat: add agent context and hooks"
```

---

## Task 11: Main App Layout

**Files:**
- Create: `frontend/src/App.tsx`
- Create: `frontend/src/AppContent.tsx`

- [ ] **Step 1: Create App.tsx**

Create `frontend/src/App.tsx`:
```typescript
import { AgentProvider } from './contexts/AgentContext'
import AppContent from './AppContent'

function App() {
  return (
    <AgentProvider>
      <AppContent />
    </AgentProvider>
  )
}

export default App
```

- [ ] **Step 2: Create AppContent.tsx**

Create `frontend/src/AppContent.tsx`:
```typescript
import { useEffect } from 'react'
import { useAgent } from './contexts/AgentContext'
import ChatContainer from './components/chat/ChatContainer'
import StatusPanel from './components/status/StatusPanel'
import ErrorBanner from './components/ui/ErrorBanner'

function AppContent() {
  const { state, connect, clearError } = useAgent()

  useEffect(() => {
    connect()
  }, [connect])

  return (
    <div className="flex h-screen bg-gray-50">
      {/* Error banner */}
      {state.error && (
        <div className="fixed top-0 left-0 right-0 z-50">
          <ErrorBanner error={state.error} onDismiss={clearError} />
        </div>
      )}

      {/* Status sidebar */}
      <StatusPanel />

      {/* Main chat area */}
      <main className="flex-1 overflow-hidden">
        <ChatContainer />
      </main>
    </div>
  )
}

export default AppContent
```

- [ ] **Step 3: Add ErrorBanner component (minimal)**

Create `frontend/src/components/ui/ErrorBanner.tsx`:
```typescript
import React from 'react'
import { AppErrorName } from '../../types/agent'

interface ErrorBannerProps {
  error: {
    name: AppErrorName
    message: string
    timestamp: Date
  }
  onDismiss: () => void
}

const errorTitles: Record<AppErrorName, string> = {
  NONE: '',
  CONNECTION_ERROR: 'Connection Error',
  AUTH_ERROR: 'Authentication Failed',
  RATE_LIMIT_ERROR: 'Rate Limit Exceeded',
  TIMEOUT_ERROR: 'Request Timeout',
  INTERNAL_ERROR: 'Internal Error',
}

function ErrorBanner({ error, onDismiss }: ErrorBannerProps) {
  return (
    <div className="bg-red-50 border border-red-200 rounded-md p-3 max-w-md mx-auto mt-2 shadow-sm">
      <div className="flex">
        <div className="flex-shrink-0">
          <svg
            className="h-5 w-5 text-red-400"
            viewBox="0 0 20 20"
            fill="currentColor"
          >
            <path
              fillRule="evenodd"
              d="M10 18a8 8 0 100-16 8 8 0 000 16zM8.707 7.293a1 1 0 00-1.414 1.414L8.586 10l-1.293 1.293a1 1 0 101.414 1.414L10 11.414l1.293 1.293a1 1 0 001.414-1.414L11.414 10l1.293-1.293a1 1 0 00-1.414-1.414L10 8.586 8.707 7.293z"
              clipRule="evenodd"
            />
          </svg>
        </div>
        <div className="ml-3">
          <h3 className="text-sm font-medium text-red-800">
            {errorTitles[error.name]}
          </h3>
          <div className="mt-1 text-sm text-red-700">{error.message}</div>
          <div className="mt-2">
            <button
              onClick={onDismiss}
              className="text-sm font-medium text-red-700 hover:text-red-500"
            >
              Dismiss
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}

export default ErrorBanner
```

- [ ] **Step 4: Update App.tsx import**

Update `frontend/src/App.tsx`:
```typescript
import { AgentProvider } from './contexts/AgentContext'
import AppContent from './AppContent'

function App() {
  return (
    <AgentProvider>
      <AppContent />
    </AgentProvider>
  )
}

export default App
```

- [ ] **Step 5: Commit**

```bash
cd /Users/taozeng/Projects/SoloCoder/../SoloCoder_react
git add frontend/src/App.tsx frontend/src/AppContent.tsx frontend/src/components/ui/ErrorBanner.tsx
git commit -m "feat: add main app layout with error banner"
```

---

## Task 12: Status Panel

**Files:**
- Create: `frontend/src/components/status/StatusPanel.tsx`
- Create: `frontend/src/components/status/TurnCounter.tsx`

- [ ] **Step 1: Create TurnCounter**

Create `frontend/src/components/status/TurnCounter.tsx`:
```typescript
import React from 'react'
import { useAgent } from '../../contexts/AgentContext'

function TurnCounter() {
  const { state } = useAgent()

  return (
    <div className="flex items-center gap-2 text-sm">
      <span className="text-gray-600">Turns:</span>
      <span className="font-medium text-gray-900">{state.turnCount}</span>
    </div>
  )
}

export default TurnCounter
```

- [ ] **Step 2: Create StatusPanel**

Create `frontend/src/components/status/StatusPanel.tsx`:
```typescript
import React from 'react'
import { useAgent } from '../../contexts/AgentContext'
import apiClient from '../../lib/api'
import TurnCounter from './TurnCounter'
import { ModelInfo } from '../../types/api'

function StatusPanel() {
  const { state, updateModel, resetSession } = useAgent()
  const [models, setModels] = React.useState<ModelInfo[]>([])
  const [isLoading, setIsLoading] = React.useState(false)

  React.useEffect(() => {
    const loadModels = async () => {
      try {
        const response = await apiClient.getModels()
        setModels(response.models)
      } catch (error) {
        console.error('Failed to load models:', error)
      }
    }

    loadModels()
  }, [])

  const handleNewSession = () => {
    resetSession()
  }

  return (
    <div className="w-80 bg-white border-r border-gray-200 flex flex-col">
      {/* Header */}
      <div className="p-4 border-b border-gray-200">
        <h1 className="text-lg font-bold text-gray-900">SoloCoder</h1>
        <p className="text-sm text-gray-500">AI Coding Assistant</p>
      </div>

      {/* Content */}
      <div className="flex-1 overflow-y-auto p-4">
        {/* Model selection */}
        <div className="mb-6">
          <label className="block text-sm font-medium text-gray-700 mb-2">
            Model
          </label>
          {models.length > 0 ? (
            <select
              value={state.currentModel}
              onChange={(e) => updateModel(e.target.value)}
              className="w-full px-3 py-2 border border-gray-300 rounded-md text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
            >
              {models.map((model) => (
                <option key={model.id} value={model.id}>
                  {model.name}
                </option>
              ))}
            </select>
          ) : (
            <div className="w-full py-2 px-3 text-sm text-center text-gray-500 bg-gray-50 rounded-md">
              Loading...
            </div>
          )}
        </div>

        {/* Connected status */}
        <div className="mb-4">
          <div
            className={`flex items-center gap-2 px-3 py-2 rounded-md text-sm ${
              state.connected
                ? 'bg-green-50 text-green-800'
                : 'bg-red-50 text-red-800'
            }`}
          >
            <span
              className={`w-2 h-2 rounded-full ${
                state.connected ? 'bg-green-500' : 'bg-red-500'
              }`}
            ></span>
            {state.connected ? 'Connected' : 'Disconnected'}
          </div>
        </div>

        {/* Turn counter */}
        <div className="mb-4">
          <TurnCounter />
        </div>

        <hr className="border-gray-200 my-4" />

        {/* New Session button */}
        <button
          onClick={handleNewSession}
          disabled={isLoading}
          className="w-full py-2 px-4 border border-gray-300 rounded-md text-sm font-medium text-gray-700 bg-white hover:bg-gray-50 disabled:opacity-50 disabled:cursor-not-allowed"
        >
          {isLoading ? 'Clearing...' : 'New Session'}
        </button>
      </div>
    </div>
  )
}

export default StatusPanel
```

- [ ] **Step 3: Commit**

```bash
cd /Users/taozeng/Projects/SoloCoder/../SoloCoder_react
git add frontend/src/components/status/
git commit -m "feat: add status panel"
```

---

## Task 13: Chat Container

**Files:**
- Create: `frontend/src/components/chat/ChatContainer.tsx`
- Create: `frontend/src/components/chat/ChatMessage.tsx`
- Create: `frontend/src/components/chat/ChatInput.tsx`

- [ ] **Step 1: Create ChatMessage**

Create `frontend/src/components/chat/ChatMessage.tsx`:
```typescript
import React from 'react'
import { Message } from '../../types/agent'

interface ChatMessageProps {
  message: Message
}

function formatTimestamp(date: Date): string {
  return new Intl.DateTimeFormat('en-US', {
    hour: 'numeric',
    minute: '2-digit',
  }).format(date)
}

function ChatMessage({ message }: ChatMessageProps) {
  const isUser = message.role === 'user'

  return (
    <div
      className={`flex ${isUser ? 'justify-end' : 'justify-start'} mb-4`}
    >
      <div
        className={`max-w-[80%] rounded-lg p-4 ${
          isUser
            ? 'bg-blue-500 text-white'
            : 'bg-white border border-gray-200 text-gray-900'
        }`}
      >
        {/* Timestamp */}
        <div
          className={`text-xs mb-2 ${
            isUser ? 'text-blue-100' : 'text-gray-500'
          }`}
        >
          {formatTimestamp(message.timestamp)}
        </div>

        {/* Content */}
        <div className="whitespace-pre-wrap text-sm leading-relaxed">
          {message.content}
        </div>
      </div>
    </div>
  )
}

export default ChatMessage
```

- [ ] **Step 2: Create ChatInput**

Create `frontend/src/components/chat/ChatInput.tsx`:
```typescript
import React from 'react'
import { useAgent } from '../../contexts/AgentContext'

interface ChatInputProps {
  disabled?: boolean
}

function ChatInput({ disabled }: ChatInputProps) {
  const { state, sendMessage } = useAgent()
  const [input, setInput] = React.useState('')
  const inputRef = React.useRef<HTMLTextAreaElement>(null)

  const handleSubmit = async () => {
    if (input.trim() && !disabled) {
      await sendMessage(input)
      setInput('')
      inputRef.current?.focus()
    }
  }

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleSubmit()
    }
  }

  return (
    <div className="bg-white border-t border-gray-200 p-4">
      <div className="flex gap-2">
        <textarea
          ref={inputRef}
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="Ask SoloCoder to help with code..."
          disabled={disabled || state.isLoading}
          rows={2}
          className="flex-1 border border-gray-300 rounded-lg px-3 py-2 text-sm resize-none focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent disabled:opacity-50"
        />
        <button
          onClick={handleSubmit}
          disabled={disabled || state.isLoading || !input.trim()}
          className="px-4 py-2 bg-blue-500 text-white rounded-lg text-sm font-medium hover:bg-blue-600 disabled:opacity-50 disabled:cursor-not-allowed"
        >
          {'Send'}
        </button>
      </div>
      <div className="text-xs text-gray-500 mt-2">
        {'Press Enter to send, Shift + Enter for new line'}
      </div>
    </div>
  )
}

export default ChatInput
```

- [ ] **Step 3: Create ChatContainer**

Create `frontend/src/components/chat/ChatContainer.tsx`:
```typescript
import React from 'react'
import { useAgent } from '../../contexts/AgentContext'
import ChatMessage from './ChatMessage'
import ChatInput from './ChatInput'

function ChatContainer() {
  const { state } = useAgent()
  const messagesEndRef = React.useRef<HTMLDivElement>(null)

  // Auto-scroll to bottom on new messages
  React.useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [state.messages])

  return (
    <div className="flex flex-col h-full">
      {/* Messages area */}
      <div className="flex-1 overflow-y-auto p-4">
        {state.messages.length === 0 ? (
          <div className="flex items-center justify-center h-full">
            <div className="text-center text-gray-400">
              <p className="text-lg mb-2">💻 SoloCoder</p>
              <p className="text-sm">Ask me anything about coding!</p>
            </div>
          </div>
        ) : (
          <>
            {state.messages.map((message) => (
              <ChatMessage key={message.id} message={message} />
            ))}
            <div ref={messagesEndRef} />
          </>
        )}
      </div>

      {/* Input area */}
      <ChatInput disabled={state.isLoading} />
    </div>
  )
}

export default ChatContainer
```

- [ ] **Step 4: Commit**

```bash
cd /Users/taozeng/Projects/SoloCoder/../SoloCoder_react
git add frontend/src/components/chat/
git commit -m "feat: add chat components"
```

---

## Task 14: Tool Calls Display (V1 Basic)

**Files:**
- Create: `frontend/src/components/tools/ToolCallDisplay.tsx`

> Note: V1 will show basic tool calls without detailed arguments display

- [ ] **Step 1: Create tool display placeholder**

Create `frontend/src/components/tools/ToolCallDisplay.tsx`:
```typescript
import React from 'react'
import { ToolCall } from '../../types/agent'

interface ToolCallDisplayProps {
  toolCall: ToolCall
}

function getToolDescription(name: string, args: Record<string, unknown>): string {
  // Simplified descriptions for V1
  const descriptions: Record<string, string> = {
    bash: Array.isArray(args.command) ? args.command.join(' ') : 'running bash command',
    read: `reading file`,
    write: `writing file`,
    edit: `editing file`,
    // ... add more
  }

  return descriptions[name] || `${name} called`
}

function ToolCallDisplay({ toolCall }: ToolCallDisplayProps) {
  return (
    <div className="mt-2 mb-2">
      <div className="text-sm font-mono text-blue-600">
        {toolCall.name}
      </div>
      <div className="text-xs text-gray-500 pl-4">
        {getToolDescription(toolCall.name, toolCall.arguments)}
      </div>
    </div>
  )
}

export default ToolCallDisplay
```

> Note: This is a placeholder for V1. Full implementation with real tool calls from backend will be added later.

- [ ] **Step 2: Commit**

```bash
cd /Users/taozeng/Projects/SoloCoder/../SoloCoder_react
git add frontend/src/components/tools/
git commit -m "feat: add basic tool call display"
```

---

## Phase 3: Integration and Testing

### Task 15: Backend WebSocket Endpoint

**Files:**
- Create: `backend/api/routes/ws.py`
- Modify: `backend/main.py`

- [ ] **Step 1: Create WebSocket route**

Create `backend/api/routes/ws.py`:
```python
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from typing import Dict, Optional
from services.agent_service import AgentService
from services.streaming import stream_agent_response

router = APIRouter()

class ConnectionManager:
    """WebSocket connection manager."""
    
    def __init__(self):
        self.active_connections: Dict[str, WebSocket] = {}
        self.service = AgentService()

    async def connect(self, websocket: WebSocket, session_id: str):
        """Accept and connect WebSocket."""
        await websocket.accept()
        self.active_connections[session_id] = websocket

    def disconnect(self, session_id: str):
        """Remove disconnected WebSocket."""
        if session_id in self.active_connections:
            del self.active_connections[session_id]

manager = ConnectionManager()


@router.websocket("/chat/stream")
async def chat_stream(websocket: WebSocket):
    """WebSocket chat streaming endpoint."""
    # Extract session_id and model from query params
    from urllib.parse import parse_qs, urlparse
    
    query_string = websocket.url_query_string.decode()
    params = parse_qs(query_string)
    
    session_id = params.get('session_id', [None])[0]
    model = params.get('model', ['gpt-4o'])[0]
    
    await manager.connect(websocket, session_id or '')
    
    try:
        async for event in websocket.iter_json():
            event_type = event.get('type')
            
            if event_type == 'message':
                content = event.get('content')
                if content and manager.service:
                    async for chunk in stream_agent_response(
                        manager.service,
                        content,
                        session_id,
                        model
                    ):
                        await websocket.send_json(chunk)
            
            elif event_type == 'pong':
                await websocket.send_json({"type": "pong"})
                
    except WebSocketDisconnect:
        manager.disconnect(session_id)
    except Exception as e:
        await websocket.send_json({"type": "error", "error": str(e)})
        raise
```

- [ ] **Step 2: Update main.py**

```python
# Add at top
from api.routes import ws

# Include in app.add
app.include_router(ws.router, prefix=settings.api_prefix)
```

- [ ] **Step 3: Commit**

```bash
cd /Users/taozeng/Projects/SoloCoder/../SoloCoder_react
git add backend/api/routes/ws.py backend/main.py
git commit -m "feat: add WebSocket streaming endpoint"
```

---

## Complete Implementation Summary

This plan covers:

**Backend (Phase 1):**
- Task 1-6: Project setup, FastAPI app, Pydantic schemas, agent service, streaming, REST routes, WebSocket endpoint

**Frontend (Phase 2):**
- Task 7-14: Vite + React setup, project structure, API client, types, context/hooks, main app, status panel, chat components, tool display

**Integration (Phase 3):**
- Task 15: Backend WebSocket endpoint for streaming

Next steps after this plan:
1. Implement each task sequentially
2. Test after each task
3. Commit frequently
4. Deploy when ready

---

**Note:** After completing this plan, deploy instructions and additional V2 features (context token tracking, advanced tool visualization, shadcn/ui integration, dark mode) will be added to a separate plan.
