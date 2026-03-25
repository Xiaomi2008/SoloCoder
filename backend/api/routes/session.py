from fastapi import APIRouter
from api.schemas import SessionStatus
from services.agent_service import AgentService

router = APIRouter(prefix="/api/v1", tags=["session"])


@router.post("/session/reset")
async def reset_session():
    """Reset current session (V1 - no session tracking)."""
    return {"status": "reset", "message": "Session cleared (V1)"}


@router.get("/session/status")
async def session_status():
    """Get current session status (V1 - minimal)."""
    return SessionStatus(turn_count=0, model="gpt-4o")
