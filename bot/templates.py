"""Render message templates from config/messages.yaml, refusing to produce half-filled messages."""

import re
from string import Formatter
from typing import Dict, Optional

from .config import load_yaml

# Thai vowels written before the consonant; a label must not end with one of them.
_LEADING_VOWELS = "เแโใไ"


class TemplateError(ValueError):
    pass


def load_messages() -> Dict[str, str]:
    return load_yaml("messages.yaml")


def render(template: str, **values: Optional[str]) -> str:
    """Fill {placeholders}; raise TemplateError if any placeholder is missing or blank."""
    fields = {name for _, name, _, _ in Formatter().parse(template) if name}
    missing = sorted(f for f in fields if not str(values.get(f) or "").strip())
    if missing:
        raise TemplateError("ข้อความยังขาดค่า: " + ", ".join(missing))
    return template.format(**{f: str(values[f]).strip() for f in fields})


def shop_display_name(name: Optional[str], max_len: int = 40) -> str:
    """Shop name for messages, e.g. "ก๋วยเตี๋ยวเรือป้าแดง - Boat Noodle" -> "ร้านก๋วยเตี๋ยวเรือป้าแดง"."""
    cleaned = re.sub(r"\s+", " ", name or "").strip()
    cleaned = re.split(r"\s[-|–—]\s|\s\|", cleaned)[0].strip()  # drop taglines after " - " or " | "
    if not cleaned:
        return "ทางร้าน"
    cleaned = cleaned[:max_len].rstrip()
    if cleaned.startswith("ร้าน"):
        return cleaned
    return ("ร้าน" if "฀" <= cleaned[0] <= "๿" else "ร้าน ") + cleaned


def friend_label(code: str, name: Optional[str], max_len: int = 20) -> str:
    """Display name the bot gives a new LINE friend, e.g. "C1023 ร้านป้าแดง", cut to LINE's length limit."""
    cleaned = re.sub(r"\s+", " ", name or "").strip()
    label = f"{code} {cleaned}".strip()[:max_len].rstrip()
    return label.rstrip(_LEADING_VOWELS).rstrip()
