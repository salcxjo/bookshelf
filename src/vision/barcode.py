"""Barcode decoding: image -> validated ISBN-13."""
from pathlib import Path

import cv2
import numpy as np
from pyzbar import pyzbar


def is_valid_isbn13(code: str) -> bool:
    """Return True if `code` is a 13-digit ISBN-13 with a correct check digit."""
    if len(code) != 13 or not code.isdigit():
        return False

    # Book barcodes always start with the Bookland prefixes 978 or 979
    if code[:3] not in ("978", "979"):
        return False

    # Weights alternate 1, 3, 1, 3... over the first 12 digits
    ones_sum = sum(int(d) for d in code[:12:2])
    threes_sum = sum(int(d) * 3 for d in code[1:12:2])
    check_digit = (10 - (ones_sum + threes_sum) % 10) % 10

    return check_digit == int(code[12])


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