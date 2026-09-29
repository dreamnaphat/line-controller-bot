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
