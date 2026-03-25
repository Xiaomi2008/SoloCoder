import asyncio
import uuid
from typing import AsyncGenerator
from services.agent_service import AgentService


def generate_message_id() -> str:
    """Generate a unique message ID."""
    return str(uuid.uuid4())[:8]


async def stream_agent_response(
    agent_service: AgentService, message: str, session_id: str, model: str
) -> AsyncGenerator[dict, None]:
    """Stream agent response as async generator."""

    msg_id = generate_message_id()

    # Start message
    yield {"type": "message_start", "messageId": msg_id, "role": "assistant"}

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
                    "content": current_chunk,
                }
                current_chunk = ""
                await asyncio.sleep(0.03)

        # End message
        yield {"type": "message_end", "messageId": msg_id, "complete": True}

    except Exception as e:
        yield {"type": "error", "messageId": msg_id, "error": str(e)}
        raise
