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
        installed_models=models or [],
        message=problem(reachable, available, models or []),
    )


def problem(reachable: bool, available: bool, models: list[str]) -> str | None:
    wanted = settings.ollama_model
    if not reachable:
        return f"Ollama is not running at {settings.ollama_host}. Start it with: ollama serve"
    if available:
        return None
    similar = [name for name in models if name.split(":")[0] == wanted.split(":")[0]]
    if similar:
        return (
            f"{wanted} is not installed, but {', '.join(similar)} is. "
            f"Set OLLAMA_MODEL={similar[0]} in .env and restart, or run: ollama pull {wanted}"
        )
    return f"{wanted} is not installed. Run: ollama pull {wanted}"
