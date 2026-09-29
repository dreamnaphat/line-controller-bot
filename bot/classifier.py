"""Classify a shop's reply to the opener using the ordered keyword rules in config/reply_rules.yaml."""

import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Pattern

from .config import load_yaml


@dataclass(frozen=True)
class Classification:
    group: str
    reply: str               # message id in config/messages.yaml, e.g. "B1"
    status: str              # new outreach status
    shop_state: str          # OPEN | NOT_OPEN_YET | CLOSED | UNKNOWN
    do_not_contact: bool
    alert: bool              # tell the employee right away


@dataclass(frozen=True)
class _Rule:
    name: str
    reply: str
    status: str
    patterns: List[Pattern]
    do_not_contact: bool = False
    alert: bool = False
    shop_state: Optional[str] = None


def normalize_reply(text: Optional[str]) -> str:
    """Lowercase, drop whitespace/zero-width characters, and squeeze 3+ repeats ("เปิดดดด" -> "เปิด")."""
    t = unicodedata.normalize("NFC", text or "").lower()
    t = re.sub(r"[\s​‌‍﻿]+", "", t)
    return re.sub(r"(.)\1{2,}", r"\1", t)


def _rule(spec: Dict[str, Any]) -> _Rule:
    return _Rule(
        name=spec["name"],
        reply=spec["reply"],
        status=spec["status"],
        patterns=[re.compile(p) for p in spec.get("patterns", [])],
        do_not_contact=bool(spec.get("do_not_contact", False)),
        alert=bool(spec.get("alert", False)),
        shop_state=spec.get("shop_state"),
    )


class ReplyClassifier:
    def __init__(self, rules: Dict[str, Any]):
        self._groups = [_rule(g) for g in rules["groups"]]
        self._default = _rule(rules["default"])
        self._open = [re.compile(p) for p in rules.get("open_patterns", [])]

    @classmethod
    def from_config(cls) -> "ReplyClassifier":
        return cls(load_yaml("reply_rules.yaml"))

    def classify(self, text: Optional[str]) -> Classification:
        normalized = normalize_reply(text)
        rule = next((g for g in self._groups if any(p.search(normalized) for p in g.patterns)), self._default)
        shop_state = rule.shop_state
        if shop_state is None:
            shop_state = "OPEN" if any(p.search(normalized) for p in self._open) else "UNKNOWN"
        return Classification(rule.name, rule.reply, rule.status, shop_state, rule.do_not_contact, rule.alert)
