from typing import Literal

from pydantic import BaseModel


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    ollama_reachable: bool
    model_available: bool
    model: str
    installed_models: list[str] = []
    message: str | None = None
