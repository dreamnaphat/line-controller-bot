"""How many shops to contact per day: a fixed number, or the queue spread so each monthly batch is done by month end.

New shops are scanned on the 1st of every month, so a batch imported in a month should be contacted before the
next scan: its deadline is the last day of that month. A batch imported with less than `min_days` left in the
month gets until the end of the following month instead (e.g. the first import when the bot goes live mid-month).
"""

import math
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Collection, Dict, Optional, Sequence

from . import db
from .schedule import start_of_day


def month_end(day: date) -> date:
    first_of_next = (day.replace(day=28) + timedelta(days=4)).replace(day=1)
    return first_of_next - timedelta(days=1)


def deadline_for(imported: date, min_days: int) -> date:
    end = month_end(imported)
    if (end - imported).days + 1 < min_days:
        end = month_end(end + timedelta(days=1))
    return end


@dataclass(frozen=True)
class QuotaPlan:
    quota: int                  # shops to search today
    total: int                  # shops left to search, counting the ones already searched today
    deadline: Optional[date]    # auto: the deadline that sets today's pace
    due: int                    # auto: shops that must be done by that deadline
    days_left: int              # auto: days until that deadline, today included
    capped: bool                # auto: the pace hit max_daily_quota, so the batch will run late

    @property
    def days_to_finish(self) -> int:
        return math.ceil(self.total / self.quota) if self.quota else 0


@dataclass(frozen=True)
class QuotaRule:
    fixed: Optional[int] = None    # None = auto
    min_days: int = 14
    cap: int = 25

    @classmethod
    def from_config(cls, outreach: Dict[str, Any]) -> "QuotaRule":
        value = outreach.get("daily_quota", "auto")
        fixed = None if str(value).strip().lower() == "auto" else int(value)
        return cls(fixed, int(outreach.get("min_days_this_month", 14)), int(outreach.get("max_daily_quota", 25)))

    def describe(self) -> str:
        if self.fixed is not None:
            return f"โควตา {self.fixed} ร้าน/วัน"
        return f"โควตาอัตโนมัติ (ทักร้านแต่ละรอบให้หมดภายในสิ้นเดือน, ไม่เกิน {self.cap} ร้าน/วัน)"

    def plan(self, imported: Sequence[date], today: date) -> QuotaPlan:
        """`imported` holds the import day of every shop left at the start of today (searched today or still queued),
        so the number stays the same all day while the queue shrinks.

        Auto: the slowest pace that still meets every deadline when older batches go first — for each deadline,
        the shops due by then divided by the days left until then; a missed day raises the following days' pace.
        """
        total = len(imported)
        if self.fixed is not None:
            return QuotaPlan(self.fixed, total, None, 0, 0, False)
        per_deadline = Counter(deadline_for(day, self.min_days) for day in imported)
        need, deadline, due, days_left, cumulative = 1, None, 0, 0, 0
        for end in sorted(per_deadline):
            cumulative += per_deadline[end]
            days = max(1, (end - today).days + 1)
            pace = math.ceil(cumulative / days)
            if deadline is None or pace > need:
                need, deadline, due, days_left = max(pace, 1), end, cumulative, days
        return QuotaPlan(min(need, self.cap), total, deadline, due, days_left, need > self.cap)


def today_plan(conn, rule: QuotaRule, only_phones: Optional[Collection[str]], now: datetime) -> QuotaPlan:
    imported = db.quota_basis(conn, only_phones, start_of_day(now))
    return rule.plan([moment.astimezone(now.tzinfo).date() for moment in imported], now.date())


def describe_plan(plan: QuotaPlan, rule: QuotaRule) -> str:
    if rule.fixed is not None or plan.deadline is None:
        return f"โควตาวันนี้ {plan.quota} ร้าน (คิวเหลือ {plan.total} ร้าน ≈ {plan.days_to_finish} วัน)"
    text = f"โควตาวันนี้ {plan.quota} ร้าน ({plan.due} ร้านต้องทักให้ทันวันที่ {plan.deadline:%d/%m} อีก {plan.days_left} วัน"
    if plan.due != plan.total:
        text += f" · คิวเหลือทั้งหมด {plan.total} ร้าน"
    text += ")"
    if plan.capped:
        text += f" — ติดเพดาน {rule.cap} ร้าน/วัน จะช้ากว่ากำหนด"
    return text
