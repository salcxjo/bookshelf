"""Barcode decoding: image -> validated ISBN-13."""
from pathlib import Path

import cv2
import numpy as np
from pyzbar import pyzbar

from bookshelf.isbn import is_valid_isbn13  # noqa: F401  (re-exported for existing imports)


def decode_isbn_from_array(img: np.ndarray) -> str | None:
    """Find a valid ISBN-13 barcode in a BGR image array, or return None."""
    if img is None:
        raise ValueError("Provided image array is None")

    gray_img = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

    for barcode in pyzbar.decode(gray_img):
        # Skip the 5-digit price add-on and any other symbology
        if barcode.type != "EAN13":
            continue

        code = barcode.data.decode("utf-8")
        if is_valid_isbn13(code):
            return code

    return None


def decode_isbn_from_bytes(data: bytes) -> str | None:
    """Decode raw image bytes (e.g. an upload), then look for an ISBN.

    Raises ValueError if the bytes are not a readable image.
    """
    img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError("Could not decode image from the provided bytes")

    return decode_isbn_from_array(img)


def decode_isbn_from_file(image_path: str | Path) -> str | None:
    """Load an image from a real path (absolute or relative to cwd) and decode it."""
    img = cv2.imread(str(image_path))
    if img is None:
        raise FileNotFoundError(f"Could not load image at path: {image_path}")

    return decode_isbn_from_array(img)
