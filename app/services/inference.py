import base64
from pathlib import Path

import httpx
from pydantic import ValidationError

from app.config import settings
from app.models.xray import ModelFindings

PROMPT = (
    "You are assisting a clinician by screening a chest X-ray. "
    "Report in JSON: "
    "findings, one short sentence per radiographic observation; "
    "flagged_regions, the names of anatomical regions that look abnormal, "
    "such as 'right upper zone', or an empty list if none do; "
    "abnormal, true if any finding is abnormal; "
    "confidence, how sure you are of this reading. "
    "Describe what is visible. Do not give a diagnosis."
)


class ModelUnavailable(Exception):
    pass


class InferenceTimeout(Exception):
    pass


class InvalidModelOutput(Exception):
    pass


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


def model_ready() -> bool:
    models = installed_models()
    return models is not None and model_installed(models)


def analyze(image_path: Path) -> ModelFindings:
    image = base64.b64encode(image_path.read_bytes()).decode()
    payload = {
        "model": settings.ollama_model,
        "stream": False,
        "format": ModelFindings.model_json_schema(),
        "options": {"temperature": 0},
        "messages": [{"role": "user", "content": PROMPT, "images": [image]}],
    }
    try:
        response = httpx.post(
            f"{settings.ollama_host}/api/chat",
            json=payload,
            timeout=httpx.Timeout(settings.inference_timeout_seconds, connect=5.0),
        )
        response.raise_for_status()
    except httpx.ConnectTimeout:
        raise ModelUnavailable
    except httpx.TimeoutException:
        raise InferenceTimeout
    except httpx.HTTPError:
        raise ModelUnavailable
    try:
        return ModelFindings.model_validate_json(response.json()["message"]["content"])
    except (ValidationError, KeyError, ValueError):
        raise InvalidModelOutput
