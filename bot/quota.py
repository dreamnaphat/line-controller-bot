"""How many shops to contact per day: a fixed number, or the queue spread evenly so each monthly batch is done in time."""

import math
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Collection, Dict, Optional

from . import db
from .schedule import start_of_day


@dataclass(frozen=True)
class QuotaPlan:
    quota: int          # shops to search today
    total: int          # shops left to search, counting the ones already searched today
    days_left: int      # days left to finish the batch (auto quota only)
    capped: bool        # the auto quota hit max_daily_quota, so the batch will take longer

    @property
    def days_to_finish(self) -> int:
        return math.ceil(self.total / self.quota) if self.quota else 0


@dataclass(frozen=True)
class QuotaRule:
    fixed: Optional[int] = None    # None = auto
    finish_days: int = 30
    cap: int = 25

    @classmethod
    def from_config(cls, outreach: Dict[str, Any]) -> "QuotaRule":
        value = outreach.get("daily_quota", "auto")
        fixed = None if str(value).strip().lower() == "auto" else int(value)
        return cls(fixed, int(outreach.get("finish_within_days", 30)), int(outreach.get("max_daily_quota", 25)))

    def describe(self) -> str:
        if self.fixed is not None:
            return f"โควตา {self.fixed} ร้าน/วัน"
        return f"โควตาอัตโนมัติ (ทักคิวให้หมดใน {self.finish_days} วัน, ไม่เกิน {self.cap} ร้าน/วัน)"

    def plan(self, remaining: int, searched_today: int, oldest_import: Optional[date], today: date) -> QuotaPlan:
        """Today's searches count in, so the number stays the same all day while the queue shrinks.

        Auto: the batch is spread over what is left of `finish_days`, counted from the day the oldest
        queued shop was imported; a missed day raises the following days' quota.
        """
        total = remaining + searched_today
        if self.fixed is not None:
            return QuotaPlan(self.fixed, total, 0, False)
        elapsed = (today - oldest_import).days if oldest_import else 0
        days_left = max(1, self.finish_days - elapsed)
        needed = max(1, math.ceil(total / days_left))
        return QuotaPlan(min(needed, self.cap), total, days_left, needed > self.cap)


def today_plan(conn, rule: QuotaRule, only_phones: Optional[Collection[str]], device_id: Optional[str],
               now: datetime) -> QuotaPlan:
    since = start_of_day(now)
    remaining, oldest = db.queue_left(conn, only_phones, since)
    used = db.searches_since(conn, device_id, since)
    oldest_day = oldest.astimezone(now.tzinfo).date() if oldest else None
    return rule.plan(remaining, used, oldest_day, now.date())


def describe_plan(plan: QuotaPlan, rule: QuotaRule) -> str:
    if rule.fixed is not None:
        return f"โควตาวันนี้ {plan.quota} ร้าน (คิวเหลือ {plan.total} ร้าน ≈ {plan.days_to_finish} วัน)"
    text = f"โควตาวันนี้ {plan.quota} ร้าน (คิวเหลือ {plan.total} ร้าน ÷ เหลือ {plan.days_left} วันในรอบ {rule.finish_days} วัน)"
    if plan.capped:
        text += f" — ติดเพดาน {rule.cap} ร้าน/วัน จะใช้ราว {plan.days_to_finish} วัน"
    return text
