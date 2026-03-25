from fastapi import APIRouter
from api.schemas import ModelsResponse, ModelInfo

router = APIRouter()


@router.get("/list")
async def list_models():
    """List available models."""
    return ModelsResponse(
        models=[
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
