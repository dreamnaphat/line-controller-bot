"""Database access for Neon (PostgreSQL) via psycopg 3."""

import time
from typing import Any, Dict, Iterable, List, Set
from urllib.parse import urlparse

import psycopg

from .config import ROOT
from .importer import OutreachRow, ShopRow

SCHEMA_FILE = ROOT / "db" / "schema.sql"

# The bot only reads "Shop"; its own state lives in the line_bot schema.
_NEW_SHOPS_SQL = """
SELECT s."placeId", s."name", s."phoneNumber", s."calculatedAgeDays", s."lastScanId"
FROM public."Shop" AS s
WHERE NOT EXISTS (SELECT 1 FROM line_bot.outreach AS o WHERE o.place_id = s."placeId")
  AND (%(statuses)s::text[] IS NULL OR s."status" = ANY(%(statuses)s::text[]))
  AND (NOT %(skip_visited)s OR NOT s."visited")
  AND (%(food_types)s::text[] IS NULL OR s."foodType" = ANY(%(food_types)s::text[]))
  AND (%(max_age_days)s::int IS NULL OR s."calculatedAgeDays" <= %(max_age_days)s::int)
ORDER BY s."calculatedAgeDays" ASC NULLS LAST, s."firstFoundAt" DESC
"""

_INSERT_OUTREACH_SQL = """
INSERT INTO line_bot.outreach (place_id, shop_name, age_days, scan_id, phone_raw, phone, invalid_reason, status)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT DO NOTHING
"""


def connect(url: str, attempts: int = 3) -> psycopg.Connection:
    """Connect, retrying because an idle Neon compute takes a moment to wake up."""
    for attempt in range(1, attempts + 1):
        try:
            return psycopg.connect(url, connect_timeout=15)
        except psycopg.OperationalError:
            if attempt == attempts:
                raise
            time.sleep(2 * attempt)
    raise AssertionError("unreachable")


def describe(url: str) -> str:
    """Host and database name for log output, without credentials."""
    parsed = urlparse(url)
    return f"{parsed.hostname}/{parsed.path.lstrip('/')}"


def apply_schema(conn: psycopg.Connection) -> None:
    conn.execute(SCHEMA_FILE.read_text(encoding="utf-8"))
    conn.commit()


def schema_exists(conn: psycopg.Connection) -> bool:
    return conn.execute("SELECT to_regclass('line_bot.outreach')").fetchone()[0] is not None


def fetch_new_shops(conn: psycopg.Connection, import_cfg: Dict[str, Any]) -> List[ShopRow]:
    params = {
        "statuses": import_cfg.get("shop_status") or None,
        "skip_visited": bool(import_cfg.get("skip_visited", True)),
        "food_types": import_cfg.get("food_types") or None,
        "max_age_days": import_cfg.get("max_age_days"),
    }
    return [ShopRow(*row) for row in conn.execute(_NEW_SHOPS_SQL, params).fetchall()]


def fetch_existing_phones(conn: psycopg.Connection) -> Set[str]:
    return {row[0] for row in conn.execute("SELECT phone FROM line_bot.outreach WHERE phone IS NOT NULL")}


def count_outreach(conn: psycopg.Connection) -> int:
    return conn.execute("SELECT count(*) FROM line_bot.outreach").fetchone()[0]


def insert_outreach(conn: psycopg.Connection, rows: Iterable[OutreachRow]) -> int:
    """Insert rows (skipping shops or phones that already exist) and return how many were added."""
    before = count_outreach(conn)
    with conn.cursor() as cur:
        cur.executemany(_INSERT_OUTREACH_SQL, [
            (r.place_id, r.shop_name, r.age_days, r.scan_id, r.phone_raw, r.phone, r.invalid_reason, r.status)
            for r in rows
        ])
    return count_outreach(conn) - before


TEST_PREFIX = "TEST-"

# Re-adding a test shop resets it to a fresh PENDING row so the same team number can be tested again.
_ADD_TEST_SHOP_SQL = """
INSERT INTO line_bot.outreach (place_id, shop_name, phone_raw, phone, status, notes)
VALUES (%(place_id)s, %(name)s, %(phone)s, %(phone)s, 'PENDING', 'ร้านทดสอบ')
ON CONFLICT (place_id) DO UPDATE SET
    shop_name = EXCLUDED.shop_name, status = 'PENDING', shop_state = NULL, reply_group = NULL,
    opener_variant = NULL, line_name = NULL, needs_human = false, do_not_contact = false, attempts = 0,
    last_error = NULL, added_at = NULL, messaged_at = NULL, first_reply_at = NULL, pitched_at = NULL,
    last_reply_at = NULL, last_reply_text = NULL, updated_at = now()
RETURNING code
"""


def add_test_shop(conn: psycopg.Connection, phone: str, name: str) -> str:
    """Queue a fake shop for a team phone number and return its code (e.g. "C263")."""
    params = {"place_id": TEST_PREFIX + phone, "name": name, "phone": phone}
    return conn.execute(_ADD_TEST_SHOP_SQL, params).fetchone()[0]


def clear_test_shops(conn: psycopg.Connection) -> int:
    """Delete every test shop and its message history; return how many shops were removed."""
    pattern = TEST_PREFIX + "%"
    conn.execute("DELETE FROM line_bot.messages WHERE outreach_id IN "
                 "(SELECT id FROM line_bot.outreach WHERE place_id LIKE %s)", (pattern,))
    return conn.execute("DELETE FROM line_bot.outreach WHERE place_id LIKE %s", (pattern,)).rowcount
