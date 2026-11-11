"""Text cleanup applied to every extracted block."""

import re
import unicodedata

# Control characters except tab and newline (form feeds, NULs, escape codes...).
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
_INLINE_SPACE = re.compile(r"[ \t]+")
_MANY_BLANK_LINES = re.compile(r"\n{3,}")


def normalize_text(text: str) -> str:
    """Unicode NFKC (ligatures, full-width forms), unified newlines, no control
    characters, single spaces within lines, at most one blank line in a row."""
    text = unicodedata.normalize("NFKC", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _CONTROL.sub(" ", text)
    lines = [_INLINE_SPACE.sub(" ", line).strip() for line in text.split("\n")]
    return _MANY_BLANK_LINES.sub("\n\n", "\n".join(lines)).strip()
