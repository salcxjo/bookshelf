import sys
from pathlib import Path

import cv2
from pyzbar import pyzbar

def is_valid_isbn13(code: str) -> bool:
    if len(code) != 13 or not code.isdigit(): return False
    ones = code[:12:2]
    threes = code[1:12:2]
    onesSum=0
    threesSum=0
    for num in ones:onesSum+=int(num)
    for num in threes:threesSum+=int(num)*3
    total = onesSum+threesSum
    if (10 - (total % 10)) % 10 != int(code[12]): return False
    if (str(code[0:3])!="978") and (str(code[0:3])!="979"):
        return False
    return True

def decode_isbn(image_path: str) -> str | None:
    script_dir = Path(__file__).parent.parent.parent
    file_path = script_dir / "data" / "testBarcodes" /image_path
    img = cv2.imread(file_path)

    if img is None:
        raise FileNotFoundError(image_path)
    gray_img = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

    barcodes = pyzbar.decode(gray_img)
    for barcode in pyzbar.decode(gray_img):
        if barcode.type != "EAN13":
            continue
        code = barcode.data.decode("utf-8")
        if is_valid_isbn13(code):
            return code
    return None