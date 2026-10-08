import base64
import binascii
import os
from io import BytesIO
from pathlib import Path

import numpy as np
import pydicom
from PIL import Image, ImageChops, ImageOps, ImageStat, UnidentifiedImageError
from pydicom.errors import InvalidDicomError
from pydicom.pixels import apply_modality_lut, apply_voi_lut

from app.config import settings

SIDE_VIEWS = {"LL", "RL", "LATERAL"}
CHEST_PARTS = {"", "CHEST", "THORAX", "LUNG"}


class InvalidImage(Exception):
    error = "invalid_image"


class SideView(InvalidImage):
    error = "not_frontal_view"


def decode(encoded: str) -> Image.Image:
    if encoded.startswith("data:"):
        encoded = encoded.partition(",")[2]
    try:
        raw = base64.b64decode(encoded, validate=True)
    except binascii.Error:
        raise InvalidImage("The image could not be opened. Please choose or take the picture again.")
    if is_dicom(raw):
        image = read_dicom(raw)
    else:
        image = read_picture(raw)
    if colour_spread(image) > settings.max_colour_spread:
        raise InvalidImage("This is a colour photo. Please upload the X-ray image itself.")
    return image


def is_dicom(raw: bytes) -> bool:
    return raw[128:132] == b"DICM"


def read_dicom(raw: bytes) -> Image.Image:
    try:
        dataset = pydicom.dcmread(BytesIO(raw))
    except (InvalidDicomError, OSError, ValueError):
        raise InvalidImage("This DICOM file is damaged and could not be opened.")
    body_part = str(dataset.get("BodyPartExamined", "")).strip().upper()
    side_view = str(dataset.get("ViewPosition", "")).strip().upper() in SIDE_VIEWS
    if side_view and body_part in CHEST_PARTS:
        raise SideView
    try:
        pixels = dataset.pixel_array
    except Exception:
        raise InvalidImage(
            "This DICOM file could not be opened. Please export it as uncompressed DICOM, PNG or JPEG."
        )
    if dataset.get("NumberOfFrames", 1) > 1:
        pixels = pixels[0]
    if dataset.get("SamplesPerPixel", 1) > 1:
        return Image.fromarray(pixels.astype(np.uint8)).convert("RGB")
    pixels = apply_voi_lut(apply_modality_lut(pixels, dataset), dataset).astype(np.float64)
    low, high = pixels.min(), pixels.max()
    pixels = (pixels - low) * (255 / ((high - low) or 1))
    if dataset.get("PhotometricInterpretation") == "MONOCHROME1":
        pixels = 255 - pixels
    return Image.fromarray(pixels.round().astype(np.uint8)).convert("RGB")


def read_picture(raw: bytes) -> Image.Image:
    try:
        image = Image.open(BytesIO(raw))
        image.load()
    except (UnidentifiedImageError, OSError):
        raise InvalidImage("This file is not an image. Please upload a PNG, JPEG, TIFF, BMP, WebP, GIF or DICOM file.")
    return to_rgb(ImageOps.exif_transpose(image))


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
