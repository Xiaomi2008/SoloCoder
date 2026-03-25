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
            response = await asyncio.wait_for(
                agent.run(message),
                timeout=timeout
            )

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
