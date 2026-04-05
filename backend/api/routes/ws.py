from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from typing import Dict, Optional
from urllib.parse import parse_qs

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
    query_string = websocket.url_query_string.decode()
    params = parse_qs(query_string)

    session_id = params.get("session_id", [None])[0]
    model = params.get("model", ["gpt-4o"])[0]

    await manager.connect(websocket, session_id or "")

    try:
        async for event in websocket.iter_json():
            event_type = event.get("type")

            if event_type == "message":
                content = event.get("content")
                if content and manager.service:
                    async for chunk in stream_agent_response(
                        manager.service,
                        content,
                        session_id,
                        model,
                    ):
                        await websocket.send_json(chunk)

            elif event_type == "pong":
                await websocket.send_json({"type": "pong"})

    except WebSocketDisconnect:
        manager.disconnect(session_id)
    except Exception as e:
        await websocket.send_json({"type": "error", "error": str(e)})
        raise
