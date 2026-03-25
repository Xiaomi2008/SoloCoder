from fastapi import APIRouter
from api.schemas import ModelsResponse, ModelInfo

router = APIRouter(prefix="/api/v1", tags=["models"])


@router.get("/models/list")
async def list_models():
    """List available models."""
    return ModelsResponse(
        models=[
            ModelInfo(
                id="qwen3.5-35b-a3b",
                name="Qwen3.5-35B-A3B",
                provider="LM Studio (Local)",
                description="Qwen3.5 35B model via LM Studio local server",
            ),
            ModelInfo(
                id="gpt-4o",
                name="GPT-4o",
                provider="OpenAI",
                description="OpenAI's fastest, most capable model",
            ),
            ModelInfo(
                id="gpt-4-turbo",
                name="GPT-4 Turbo",
                provider="OpenAI",
                description="Cost-effective GPT-4 Turbo model",
            ),
        ]
    )
