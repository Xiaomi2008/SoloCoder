class AgentService:
    """Stub for AgentService to enable streaming tests."""

    async def get_or_create_session(self, session_id: str, model: str) -> "Agent":
        """Create or retrieve agent session."""
        return Agent()


class Agent:
    """Stub for Agent class."""

    async def run(self, message: str) -> str:
        """Run agent with message."""
        return f"Response to: {message}"
