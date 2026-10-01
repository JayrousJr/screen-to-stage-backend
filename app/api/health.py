from fastapi import APIRouter

from app.config import settings
from app.models.health import HealthResponse
from app.services import inference

router = APIRouter()


@router.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    models = inference.installed_models()
    reachable = models is not None
    available = reachable and inference.model_installed(models)
    return HealthResponse(
        status="ok" if available else "degraded",
        ollama_reachable=reachable,
        model_available=available,
        model=settings.ollama_model,
    )
