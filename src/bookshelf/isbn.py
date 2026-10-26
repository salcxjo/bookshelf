"""Pure ISBN helpers (no OpenCV, no network)."""
import re


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


def is_valid_isbn10(code: str) -> bool:
    """Weights 10..1 over the digits; the last character may be X (= 10)."""
    if not re.fullmatch(r"\d{9}[\dXx]", code):
        return False
    total = sum((10 - i) * (10 if ch in "Xx" else int(ch)) for i, ch in enumerate(code))
    return total % 11 == 0


def isbn10_to_13(code10: str) -> str:
    """'0306406152' -> '9780306406157'. The input must already be a valid ISBN-10."""
    body = "978" + code10[:9]
    ones_sum = sum(int(d) for d in body[::2])
    threes_sum = sum(int(d) * 3 for d in body[1::2])
    return body + str((10 - (ones_sum + threes_sum) % 10) % 10)


def normalize_isbn(text: str | None) -> str | None:
    """Turn typed input such as '978-0-306-40615-7' or '0 306 40615 2' into a valid
    13-digit ISBN, or None if it isn't one."""
    cleaned = re.sub(r"[\s-]", "", text or "")
    if is_valid_isbn13(cleaned):
        return cleaned
    if is_valid_isbn10(cleaned):
        return isbn10_to_13(cleaned.upper())
    return None
