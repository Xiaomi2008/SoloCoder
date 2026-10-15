from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
from config import settings
from api.routes import chat, models, session
from api.routes.ws import manager, chat_stream


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


# Include REST routers (they already have their own prefixes)
app.include_router(chat.router)
app.include_router(models.router)
app.include_router(session.router)

# Add WebSocket route directly
app.websocket("/api/v1/chat/stream")(chat_stream)
