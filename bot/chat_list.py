"""Read the LINE chat list (Chats tab) from a uiautomator2 hierarchy dump.

The list is a Compose screen with almost no resource ids, so rows are rebuilt from screen bounds:
the clickable row that encloses a "C12 ..." name also encloses the last-message preview and,
when there are unread messages, an `unread_count` badge with the number in it.
"""

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import List, Optional, Tuple

from .notifications import outreach_id_from_title

Bounds = Tuple[int, int, int, int]
_BOUNDS = re.compile(r"\[(-?\d+),(-?\d+)\]\[(-?\d+),(-?\d+)\]")
_TIME_LIKE = re.compile(r"^(\d{1,2}:\d{2}|\d{1,2}/\d{1,2}(/\d{2,4})?|เมื่อวาน|Yesterday)$")


@dataclass(frozen=True)
class ChatRow:
    title: str
    outreach_id: int
    preview: str
    unread: int


@dataclass(frozen=True)
class _Node:
    text: str
    rid: str
    clickable: bool
    bounds: Bounds


def _bounds(raw: str) -> Optional[Bounds]:
    match = _BOUNDS.fullmatch(raw or "")
    return tuple(int(v) for v in match.groups()) if match else None


def _inside(inner: Bounds, outer: Bounds) -> bool:
    return outer[0] <= inner[0] and outer[1] <= inner[1] and inner[2] <= outer[2] and inner[3] <= outer[3]


def _area(b: Bounds) -> int:
    return (b[2] - b[0]) * (b[3] - b[1])


def _unread(text: str) -> int:
    digits = re.sub(r"\D", "", text)
    return int(digits) if digits else 0


def parse_chat_list(xml: str) -> List[ChatRow]:
    """Rows whose chat name starts with one of our codes; other chats are ignored."""
    nodes = []
    for el in ET.fromstring(xml).iter("node"):
        bounds = _bounds(el.get("bounds", ""))
        if bounds:
            nodes.append(_Node(el.get("text", ""), el.get("resource-id", ""), el.get("clickable") == "true", bounds))

    rows = []
    for name in nodes:
        outreach_id = outreach_id_from_title(name.text)
        if outreach_id is None:
            continue
        containers = [n for n in nodes if n.clickable and n is not name and _inside(name.bounds, n.bounds)]
        if not containers:
            continue
        row = min(containers, key=lambda n: _area(n.bounds))
        inside = [n for n in nodes if n is not name and _inside(n.bounds, row.bounds)]
        badges = [n for n in inside if n.rid.endswith("unread_count")]
        badge_texts = [n for n in inside if n.text and any(_inside(n.bounds, b.bounds) for b in badges)]
        unread = _unread(badge_texts[0].text) if badge_texts else 0
        previews = [n for n in inside
                    if n.text and n not in badge_texts and not _TIME_LIKE.match(n.text.strip())
                    and n.bounds[1] >= name.bounds[3] - 10]
        preview = min(previews, key=lambda n: n.bounds[1]).text if previews else ""
        rows.append(ChatRow(name.text, outreach_id, preview, unread))
    return rows
