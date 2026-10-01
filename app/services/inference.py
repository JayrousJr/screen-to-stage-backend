import httpx

from app.config import settings


def installed_models() -> list[str] | None:
    try:
        response = httpx.get(f"{settings.ollama_host}/api/tags", timeout=2.0)
        response.raise_for_status()
    except httpx.HTTPError:
        return None
    return [model["name"] for model in response.json().get("models", [])]


def model_installed(models: list[str]) -> bool:
    wanted = settings.ollama_model
    return any(name == wanted or name.startswith(f"{wanted}:") for name in models)
