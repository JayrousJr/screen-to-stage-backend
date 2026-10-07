import base64
from pathlib import Path
from typing import TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from app.config import settings
from app.models.xray import ImageCheck, ModelFindings

T = TypeVar("T", bound=BaseModel)

CHECK_PROMPT = (
    "Look at this image. "
    "Report in JSON: is_chest_xray, true only if it is a radiograph (X-ray) of a human chest, "
    "false for anything else, such as a photo of a person, an object, a landscape, a document, "
    "or an X-ray of another body part."
)

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


class NotChestXray(Exception):
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


def ask(image: str, prompt: str, schema: type[T]) -> T:
    payload = {
        "model": settings.ollama_model,
        "stream": False,
        "format": schema.model_json_schema(),
        "options": {"temperature": 0},
        "messages": [{"role": "user", "content": prompt, "images": [image]}],
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
        return schema.model_validate_json(response.json()["message"]["content"])
    except (ValidationError, KeyError, ValueError):
        raise InvalidModelOutput


def analyze(image_path: Path) -> ModelFindings:
    image = base64.b64encode(image_path.read_bytes()).decode()
    if not ask(image, CHECK_PROMPT, ImageCheck).is_chest_xray:
        raise NotChestXray
    return ask(image, PROMPT, ModelFindings)
