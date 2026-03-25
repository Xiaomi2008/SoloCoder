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
    request: ChatRequest, service: AgentService = None
) -> ChatResponse:
    """Process a chat message."""
    if service is None:
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
