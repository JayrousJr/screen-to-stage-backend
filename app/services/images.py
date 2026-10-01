import base64
import binascii
import os
from io import BytesIO
from pathlib import Path

from PIL import Image, UnidentifiedImageError

from app.config import settings


class InvalidImage(Exception):
    pass


def decode(encoded: str) -> Image.Image:
    if encoded.startswith("data:"):
        encoded = encoded.partition(",")[2]
    try:
        raw = base64.b64decode(encoded, validate=True)
    except binascii.Error:
        raise InvalidImage("image is not valid base64")
    try:
        image = Image.open(BytesIO(raw))
        image.load()
    except (UnidentifiedImageError, OSError):
        raise InvalidImage("image could not be read")
    return to_rgb(image)


def to_rgb(image: Image.Image) -> Image.Image:
    if image.mode in ("F", "I") or image.mode.startswith("I;16"):
        low, high = image.getextrema()
        scale = 255 / ((high - low) or 1)
        image = image.convert("F").point(lambda value: (value - low) * scale).convert("L")
    return image.convert("RGB")


def store(encoded: str, scan_id: str) -> Path:
    image = decode(encoded)
    path = settings.image_dir / f"{scan_id}.png"
    partial = path.with_suffix(".partial")
    with open(partial, "wb") as file:
        image.save(file, "PNG")
        file.flush()
        os.fsync(file.fileno())
    os.replace(partial, path)
    return path
