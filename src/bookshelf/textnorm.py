"""Text normalisation shared by duplicate detection and matching."""
import re
import unicodedata


def norm(text: str | None) -> str:
    """Lowercase, accent-free, letters-and-digits-only form of a title or name.

    'Café Society!' -> 'cafesociety'. Arabic yeh/kaf and the zero-width joiner are
    folded, so Persian text typed on different keyboards compares equal.
    """
    s = unicodedata.normalize("NFKD", text or "")
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = s.replace("ي", "ی").replace("ك", "ک").replace("\u200c", "")
    return re.sub(r"[\W_]+", "", s.casefold())
