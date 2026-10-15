from fastapi import APIRouter
from pydantic import BaseModel
from typing import Optional
from services.agent_service import AgentService
from api.schemas import ChatRequest, ChatResponse, MessageRole, Message
import uuid
import asyncio

router = APIRouter(prefix="/api/v1", tags=["chat"])


class ChatResponseSchema(BaseModel):
    """Chat service status."""

    status: str


@router.get("/chat")
async def chat_root():
    """Chat endpoint root."""
    return {"status": "chat endpoint ready"}


@router.post("/chat/message")
async def chat_message(request: ChatRequest) -> ChatResponse:
    """Process a chat message."""
    service = AgentService()

    try:
        response_text = await service.process_message(
            message=request.message, session_id=None, model=request.model
        )
        return response_text

    except Exception as e:
        msg = Message(
            id=str(uuid.uuid4()), role=MessageRole.assistant, content=f"Error: {str(e)}"
        )
        return ChatResponse(message=msg)
