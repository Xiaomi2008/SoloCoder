from typing import AsyncGenerator


async def stream_agent_response() -> AsyncGenerator[str, None]:
    """Stream agent responses."""
    yield ""
