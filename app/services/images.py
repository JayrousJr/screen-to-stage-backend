import base64
import binascii
import os
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageChops, ImageOps, ImageStat, UnidentifiedImageError

from app.config import settings


class InvalidImage(Exception):
    pass


def decode(encoded: str) -> Image.Image:
    if encoded.startswith("data:"):
        encoded = encoded.partition(",")[2]
    try:
        raw = base64.b64decode(encoded, validate=True)
    except binascii.Error:
        raise InvalidImage("The image could not be opened. Please choose or take the picture again.")
    try:
        image = Image.open(BytesIO(raw))
        image.load()
    except (UnidentifiedImageError, OSError):
        raise InvalidImage("This file is not an image. Please upload a PNG, JPEG, TIFF, BMP, WebP or GIF.")
    image = to_rgb(ImageOps.exif_transpose(image))
    if colour_spread(image) > settings.max_colour_spread:
        raise InvalidImage("This is a colour photo. Please upload the X-ray image itself.")
    return image


def to_rgb(image: Image.Image) -> Image.Image:
    if image.mode in ("F", "I") or image.mode.startswith("I;16"):
        low, high = image.getextrema()
        scale = 255 / ((high - low) or 1)
        image = image.convert("F").point(lambda value: (value - low) * scale).convert("L")
    return image.convert("RGB")


def colour_spread(image: Image.Image) -> float:
    small = image.copy()
    small.thumbnail((256, 256))
    red, green, blue = small.split()
    high = ImageChops.lighter(ImageChops.lighter(red, green), blue)
    low = ImageChops.darker(ImageChops.darker(red, green), blue)
    return ImageStat.Stat(ImageChops.subtract(high, low)).mean[0]


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
