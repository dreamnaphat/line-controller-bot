"""Read and update outreach data for the staff dashboard (no Streamlit here, so it can be tested)."""

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Dict, Iterable, List, Optional, Tuple

import psycopg

from . import db

STATUS_LABELS = {
    "PENDING": "รอทัก", "ADDED": "เพิ่มเพื่อนแล้ว รอส่งข้อความ", "MESSAGED": "ส่งข้อความแล้ว",
    "NOT_FOUND": "ค้นไม่เจอใน LINE", "INVALID": "เบอร์ใช้ไม่ได้", "ERROR": "ทำไม่สำเร็จ ต้องให้คนดู",
    "REPLIED": "ร้านตอบแล้ว", "PITCHED": "บอทตอบกลับแล้ว", "DECLINED": "ไม่สนใจ", "CLOSED": "ปิดกิจการ",
    "NO_REPLY": "ไม่ตอบ", "INTERESTED": "สนใจ", "CUSTOMER": "เป็นลูกค้า",
}

SHOP_STATE_LABELS = {"OPEN": "เปิดแล้ว", "NOT_OPEN_YET": "ยังไม่เปิด", "CLOSED": "ปิดกิจการ", "UNKNOWN": "ไม่แน่ใจ"}


@dataclass(frozen=True)
class StaffOutcome:
    label: str
    fields: Dict[str, Any]
    needs_date: bool = False


# What a staff member can record after talking to a shop.
STAFF_OUTCOMES = {
    "talking": StaffOutcome("ยังคุยอยู่ / แค่บันทึกโน้ต", {}),
    "interested": StaffOutcome("สนใจ", {"status": "INTERESTED"}),
    "customer": StaffOutcome("เป็นลูกค้าแล้ว", {"status": "CUSTOMER"}),
    "not_open_yet": StaffOutcome("ยังไม่เปิดร้าน — นัดทักใหม่", {"shop_state": "NOT_OPEN_YET"}, needs_date=True),
    "declined": StaffOutcome("ไม่สนใจ", {"status": "DECLINED", "do_not_contact": True}),
    "closed": StaffOutcome("ปิดกิจการ", {"status": "CLOSED", "shop_state": "CLOSED", "do_not_contact": True}),
}

_SHOP_COLUMNS = ("id", "code", "shop_name", "phone", "status", "shop_state", "reply_group", "line_name",
                 "needs_human", "do_not_contact", "notes", "recontact_on", "imported_at", "searched_at",
                 "added_at", "messaged_at", "first_reply_at", "last_reply_at", "last_reply_text", "last_error",
                 "invalid_reason", "place_id")


def load_shops(conn: psycopg.Connection, include_test: bool) -> List[Dict[str, Any]]:
    rows = conn.execute(
        f"SELECT {', '.join(_SHOP_COLUMNS)} FROM line_bot.outreach"
        " WHERE %(test)s OR place_id NOT LIKE %(prefix)s ORDER BY id",
        {"test": include_test, "prefix": db.TEST_PREFIX + "%"}).fetchall()
    return [dict(zip(_SHOP_COLUMNS, row)) for row in rows]


def is_test(shop: Dict[str, Any]) -> bool:
    return shop["place_id"].startswith(db.TEST_PREFIX)


def waiting_for_staff(shops: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Shops whose replies need a person, newest reply first."""
    waiting = [s for s in shops if s["needs_human"]]
    return sorted(waiting, key=lambda s: s["last_reply_at"] or datetime.min.astimezone(), reverse=True)


def recontacts(shops: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Shops with a follow-up date, soonest first."""
    return sorted((s for s in shops if s["recontact_on"]), key=lambda s: s["recontact_on"])


FUNNEL_STEPS: List[Tuple[str, Any]] = [
    ("นำเข้า", lambda s: True),
    ("เบอร์มือถือใช้ได้", lambda s: s["status"] != "INVALID"),
    ("ค้นใน LINE แล้ว", lambda s: s["searched_at"] is not None),
    ("เพิ่มเพื่อนได้", lambda s: s["added_at"] is not None),
    ("ทักแล้ว", lambda s: s["messaged_at"] is not None),
    ("ร้านตอบ", lambda s: s["first_reply_at"] is not None),
    ("สนใจ", lambda s: s["status"] in ("INTERESTED", "CUSTOMER")),
    ("เป็นลูกค้า", lambda s: s["status"] == "CUSTOMER"),
]


def funnel(shops: Iterable[Dict[str, Any]]) -> List[Tuple[str, int]]:
    shops = list(shops)
    return [(label, sum(1 for s in shops if test(s))) for label, test in FUNNEL_STEPS]


def batch_of(shop: Dict[str, Any]) -> str:
    """The monthly import a shop came in with, e.g. "2026-11"."""
    return f"{shop['imported_at'].astimezone():%Y-%m}"


def daily_activity(shops: Iterable[Dict[str, Any]], since: date) -> Dict[date, Dict[str, int]]:
    """Per day from `since`: shops searched, messaged, and replying for the first time."""
    days: Dict[date, Dict[str, int]] = {}
    columns = {"searched_at": "ค้นเบอร์", "messaged_at": "ทักแล้ว", "first_reply_at": "ร้านตอบ"}
    for shop in shops:
        for column, label in columns.items():
            at = shop[column]
            if at is None or at.astimezone().date() < since:
                continue
            counts = days.setdefault(at.astimezone().date(), {name: 0 for name in columns.values()})
            counts[label] += 1
    return dict(sorted(days.items()))


def messages_of(conn: psycopg.Connection, outreach_id: int) -> List[Tuple[str, str, Optional[str], datetime]]:
    """(direction, sender, text, time) for one shop, oldest first."""
    return conn.execute("SELECT direction, sender, text, created_at FROM line_bot.messages"
                        " WHERE outreach_id = %s ORDER BY created_at, id", (outreach_id,)).fetchall()


def save_staff_update(conn: psycopg.Connection, outreach_id: int, outcome: str, notes: str,
                      recontact_on: Optional[date], answered: bool, do_not_contact: bool) -> None:
    """Record what a staff member found out. `answered` takes the shop off the waiting list."""
    chosen = STAFF_OUTCOMES[outcome]
    if chosen.needs_date and recontact_on is None:
        raise ValueError("ต้องใส่วันที่นัดทักใหม่")
    fields: Dict[str, Any] = {"notes": notes.strip() or None, "recontact_on": recontact_on, **chosen.fields}
    fields["do_not_contact"] = do_not_contact or chosen.fields.get("do_not_contact", False)
    if answered:
        fields["needs_human"] = False
    db.update_outreach(conn, outreach_id, **fields)
