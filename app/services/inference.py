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
    "or an X-ray of another body part; "
    "is_frontal, true if the chest is seen from the front or back (PA or AP view), "
    "false if it is seen from the side (lateral view)."
)

PROMPT = (
    "You are assisting a clinician by screening a chest X-ray. Look at the whole image carefully. "
    "Report in JSON. "
    "checklist: answer true or false for each item, true if it is visible even if subtle: "
    "consolidation, patchy or dense opacity suggesting pneumonia; "
    "tb_signs, upper zone opacity, cavities, enlarged hilar lymph nodes, miliary nodules or fibrotic scarring; "
    "pleural_effusion, fluid blunting the costophrenic angle or layering at the base; "
    "pneumothorax, a visible pleural line with no lung markings beyond it; "
    "cardiomegaly, heart wider than half the chest on a PA view; "
    "vascular_congestion, prominent upper lobe vessels, Kerley lines or interstitial oedema; "
    "nodule_or_mass, any round opacity in the lung or mediastinum; "
    "bone_abnormality, rib or clavicle fracture, lytic or sclerotic bone lesion; "
    "spine_curvature, visible scoliosis; "
    "device_misplaced, a tube, line, pacemaker or other device in the wrong position; "
    "other_abnormality, anything else abnormal. "
    "findings, one short sentence per observation, normal and abnormal; "
    "flagged_regions, the names of regions that look abnormal, such as 'right upper zone', or an empty list; "
    "devices, the tubes, lines and devices visible, or an empty list; "
    "confidence, how sure you are of this reading. "
    "When unsure whether something is abnormal, answer true so a clinician checks it. "
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


class NotFrontalView(Exception):
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


def inline_schema(schema: type[BaseModel]) -> dict:
    full = schema.model_json_schema()
    defs = full.pop("$defs", {})

    def resolve(node):
        if isinstance(node, dict):
            if "$ref" in node:
                return resolve(defs[node["$ref"].rsplit("/", 1)[-1]])
            return {key: resolve(value) for key, value in node.items()}
        if isinstance(node, list):
            return [resolve(item) for item in node]
        return node

    return resolve(full)


def ask(image: str, prompt: str, schema: type[T]) -> T:
    payload = {
        "model": settings.ollama_model,
        "stream": False,
        "format": inline_schema(schema),
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
    check = ask(image, CHECK_PROMPT, ImageCheck)
    if not check.is_chest_xray:
        raise NotChestXray
    if not check.is_frontal:
        raise NotFrontalView
    return ask(image, PROMPT, ModelFindings)
