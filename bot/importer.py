"""Turn rows from the existing "Shop" table into outreach rows (pure logic, no database access)."""

from collections import Counter
from dataclasses import dataclass
from typing import Iterable, List, Optional, Set

from .phone import normalize_phone


@dataclass(frozen=True)
class ShopRow:
    place_id: str
    name: str
    phone_raw: Optional[str]
    age_days: Optional[int]
    scan_id: Optional[str]


@dataclass(frozen=True)
class OutreachRow:
    place_id: str
    shop_name: str
    age_days: Optional[int]
    scan_id: Optional[str]
    phone_raw: Optional[str]
    phone: Optional[str]
    invalid_reason: Optional[str]
    status: str  # PENDING when the phone can be searched on LINE, otherwise INVALID


def plan_import(shops: Iterable[ShopRow], existing_phones: Set[str]) -> List[OutreachRow]:
    """Normalize phones and mark numbers already contacted (or repeated in this batch) as duplicates."""
    seen = set(existing_phones)
    rows = []
    for shop in shops:
        result = normalize_phone(shop.phone_raw)
        phone, reason = result.phone, result.reason
        if phone is not None and phone in seen:
            phone, reason = None, "duplicate"
        if phone is not None:
            seen.add(phone)
        rows.append(OutreachRow(
            place_id=shop.place_id,
            shop_name=shop.name,
            age_days=shop.age_days,
            scan_id=shop.scan_id,
            phone_raw=shop.phone_raw,
            phone=phone,
            invalid_reason=reason,
            status="PENDING" if phone is not None else "INVALID",
        ))
    return rows


def summarize(rows: Iterable[OutreachRow]) -> Counter:
    """Count rows by outcome: "PENDING" or "INVALID:<reason>"."""
    return Counter(row.status if row.status == "PENDING" else f"INVALID:{row.invalid_reason}" for row in rows)
