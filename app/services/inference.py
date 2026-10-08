import base64
from pathlib import Path
from typing import TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from app.config import settings
from app.models.xray import Box, ImageCheck, ModelComparison, Reading
from app.services.body_parts import BODY_PARTS, BodyPart

T = TypeVar("T", bound=BaseModel)

CHECK_PROMPT = (
    "Look at this image. "
    "Report in JSON: is_xray, true only if it is a medical X-ray image of a person, "
    "false for anything else, such as a photo of a person, an object, a landscape or a document; "
    "body_part, chest for a chest X-ray, bone_joint for bones, joints, limbs, spine or pelvis, "
    "abdomen for an abdominal X-ray, dental_head for teeth, jaw, face or skull, other for anything else; "
    "is_frontal, true if the image is seen from the front or back, false if it is seen from the side (lateral view)."
)

COMPARE_PROMPT = (
    "The first image is an earlier X-ray of this patient and the second image is the new one. "
    "Report in JSON: change, better if the abnormalities have improved, same if nothing has changed, "
    "worse if they have got worse, new_finding if there is something abnormal that was not there before, "
    "unclear if the images cannot be compared; "
    "summary, one or two sentences on what changed. Do not give a diagnosis."
)


class ModelUnavailable(Exception):
    pass


class InferenceTimeout(Exception):
    pass


class InvalidModelOutput(Exception):
    pass


class NotXray(Exception):
    pass


class UnsupportedBodyPart(Exception):
    pass


class NotFrontalView(Exception):
    pass


def encode(path: Path) -> str:
    return base64.b64encode(path.read_bytes()).decode()


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


def ask(images: list[str], prompt: str, schema: type[T]) -> T:
    payload = {
        "model": settings.ollama_model,
        "stream": False,
        "format": inline_schema(schema),
        "options": {"temperature": 0},
        "messages": [{"role": "user", "content": prompt, "images": images}],
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


def within_image(box: list[float]) -> bool:
    if len(box) != 4:
        return False
    x_min, y_min, x_max, y_max = box
    return 0 <= x_min < x_max <= 1000 and 0 <= y_min < y_max <= 1000


def analyze(image_path: Path) -> Reading:
    image = encode(image_path)
    check = ask([image], CHECK_PROMPT, ImageCheck)
    if not check.is_xray:
        raise NotXray
    if check.body_part not in settings.body_parts:
        raise UnsupportedBodyPart
    if check.body_part == "chest" and not check.is_frontal:
        raise NotFrontalView
    part: BodyPart = BODY_PARTS[check.body_part]
    output = ask([image], part.prompt(settings.locate_findings), part.output)
    return Reading(
        body_part=part.name,
        conditions=part.labels(output.checklist),
        findings=output.findings,
        flagged_regions=output.flagged_regions,
        devices=output.devices,
        boxes=[
            Box(label=box.label, box=[round(value) for value in box.box])
            for box in output.boxes
            if settings.locate_findings and within_image(box.box)
        ],
        confidence=output.confidence,
    )


def compare(previous_path: Path, image_path: Path) -> ModelComparison | None:
    try:
        return ask([encode(previous_path), encode(image_path)], COMPARE_PROMPT, ModelComparison)
    except (ModelUnavailable, InferenceTimeout, InvalidModelOutput, OSError):
        return None
