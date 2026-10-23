"""Database access for Neon (PostgreSQL) via psycopg 3."""

import time
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Collection, Dict, Iterable, List, Optional, Set, Tuple
from urllib.parse import urlparse

import psycopg

from .config import ROOT
from .importer import OutreachRow, ShopRow, plan_import, rows_to_store, summarize

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


def connect(url: str, attempts: int = 3, autocommit: bool = False) -> psycopg.Connection:
    """Connect, retrying because an idle Neon compute takes a moment to wake up."""
    for attempt in range(1, attempts + 1):
        try:
            return psycopg.connect(url, connect_timeout=15, autocommit=autocommit)
        except psycopg.OperationalError:
            if attempt == attempts:
                raise
            time.sleep(2 * attempt)
    raise AssertionError("unreachable")


class AutoReconnect:
    """An autocommit connection that reopens itself after Neon dropped it.

    Neon suspends its compute after 5 minutes without queries and closes open connections, so a bot that waits
    for hours between jobs finds its connection gone. A statement that fails that way is retried once.
    """

    def __init__(self, url: str, attempts: int = 3):
        self.url, self.attempts = url, attempts
        self._conn = connect(url, attempts, autocommit=True)

    def _lost(self) -> bool:
        return self._conn.closed or self._conn.broken

    def _reopen(self) -> None:
        self._conn.close()
        self._conn = connect(self.url, self.attempts, autocommit=True)

    def execute(self, query, params=None, **kwargs):
        if self._lost():
            self._reopen()
        try:
            return self._conn.execute(query, params, **kwargs)
        except psycopg.OperationalError:
            if not self._lost():
                raise  # the server answered with an error; the connection itself is fine
            self._reopen()
            return self._conn.execute(query, params, **kwargs)

    def cursor(self, *args, **kwargs):
        if self._lost():
            self._reopen()
        return self._conn.cursor(*args, **kwargs)

    def __getattr__(self, name: str):
        return getattr(self._conn, name)

    def __enter__(self) -> "AutoReconnect":
        return self

    def __exit__(self, *exc) -> None:
        self._conn.close()


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


def import_new_shops(conn: psycopg.Connection, import_cfg: Dict[str, Any]) -> Counter:
    """Queue every new shop from "Shop" (the bot's daily import) and count what was found, as summarize() does."""
    rows = plan_import(fetch_new_shops(conn, import_cfg), fetch_existing_phones(conn))
    insert_outreach(conn, rows_to_store(rows))
    return summarize(rows)


def shop_skip_reason(conn: psycopg.Connection, place_id: str, import_cfg: Dict[str, Any]) -> Optional[str]:
    """Why a queued shop must not be contacted any more because "Shop" changed after it was queued, or None.

    Sales may visit a shop or change its status during the month the bot spreads a batch over.
    """
    if place_id.startswith(TEST_PREFIX):
        return None
    row = conn.execute('SELECT "status", "visited" FROM public."Shop" WHERE "placeId" = %s', (place_id,)).fetchone()
    if row is None:
        return "removed"
    status, visited = row
    statuses = import_cfg.get("shop_status") or None
    if statuses and status not in statuses:
        return f"status:{status}"
    if import_cfg.get("skip_visited", True) and visited:
        return "visited"
    return None


TEST_PREFIX = "TEST-"

# Re-adding a test shop resets it to a fresh PENDING row so the same team number can be tested again.
_ADD_TEST_SHOP_SQL = """
INSERT INTO line_bot.outreach (place_id, shop_name, phone_raw, phone, status, notes)
VALUES (%(place_id)s, %(name)s, %(phone)s, %(phone)s, 'PENDING', 'ร้านทดสอบ')
ON CONFLICT (place_id) DO UPDATE SET
    shop_name = EXCLUDED.shop_name, status = 'PENDING', shop_state = NULL, reply_group = NULL,
    opener_variant = NULL, line_name = NULL, needs_human = false, do_not_contact = false, attempts = 0,
    last_error = NULL, added_at = NULL, messaged_at = NULL, first_reply_at = NULL, pitched_at = NULL,
    last_reply_at = NULL, last_reply_text = NULL, pending_reply = NULL, updated_at = now()
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


# ---- worker queue ----

@dataclass(frozen=True)
class QueueItem:
    id: int
    code: str
    shop_name: str
    phone: str
    status: str      # PENDING, or ADDED when a previous run stopped before the message went out
    attempts: int
    place_id: str


# Shops the worker still has to contact.
_QUEUE_WHERE = """
WHERE status IN ('PENDING', 'ADDED') AND phone IS NOT NULL AND NOT do_not_contact
  AND (%(only)s::text[] IS NULL OR phone = ANY(%(only)s::text[]))
"""

# Unfinished shops (ADDED) first, then older monthly batches (earlier deadline), then the newest shops.
_QUEUE_ORDER = "(status = 'ADDED') DESC, date_trunc('month', imported_at), age_days ASC NULLS LAST, id"

_NEXT_SQL = f"""
SELECT id, code, shop_name, phone, status, attempts, place_id FROM line_bot.outreach
{_QUEUE_WHERE}
ORDER BY {_QUEUE_ORDER}
LIMIT 1
"""

_UPDATABLE = {"status", "searched_at", "added_at", "messaged_at", "line_name", "opener_variant", "device_id",
              "last_error", "attempts", "shop_state", "reply_group", "needs_human", "do_not_contact", "first_reply_at",
              "last_reply_at", "last_reply_text", "pitched_at", "line_chat_id", "pending_reply", "notes", "recontact_on",
              "invalid_reason"}


def next_in_queue(conn: psycopg.Connection, only_phones: Optional[Collection[str]]) -> Optional[QueueItem]:
    """Next shop to contact; with `only_phones` (test mode) only shops using those numbers."""
    row = conn.execute(_NEXT_SQL, {"only": sorted(only_phones) if only_phones is not None else None}).fetchone()
    return QueueItem(*row) if row else None


def quota_basis(conn: psycopg.Connection, only_phones: Optional[Collection[str]], since: datetime) -> List[datetime]:
    """Import time of every shop left at `since`: still queued, or searched since then."""
    return [row[0] for row in conn.execute(
        "SELECT imported_at FROM line_bot.outreach"
        " WHERE (%(only)s::text[] IS NULL OR phone = ANY(%(only)s::text[]))"
        "   AND ((status IN ('PENDING', 'ADDED') AND phone IS NOT NULL AND NOT do_not_contact)"
        "        OR searched_at >= %(since)s)",
        {"only": sorted(only_phones) if only_phones is not None else None, "since": since})]


def update_outreach(conn: psycopg.Connection, outreach_id: int, **fields: Any) -> None:
    unknown = set(fields) - _UPDATABLE
    if unknown:
        raise ValueError(f"unknown outreach columns: {unknown}")
    assignments = ", ".join(f"{column} = %({column})s" for column in fields)
    conn.execute(f"UPDATE line_bot.outreach SET {assignments}, updated_at = now() WHERE id = %(id)s",
                 {**fields, "id": outreach_id})


def record_failure(conn: psycopg.Connection, outreach_id: int, error: str, max_attempts: int) -> str:
    """Count a failed attempt; after `max_attempts` the shop is parked as ERROR. Returns the new status."""
    return conn.execute(
        "UPDATE line_bot.outreach SET attempts = attempts + 1, last_error = %(error)s, updated_at = now(),"
        " status = CASE WHEN attempts + 1 >= %(max)s THEN 'ERROR' ELSE status END"
        " WHERE id = %(id)s RETURNING status",
        {"id": outreach_id, "error": error, "max": max_attempts},
    ).fetchone()[0]


def record_outgoing(conn: psycopg.Connection, outreach_id: int, text: str) -> None:
    conn.execute("INSERT INTO line_bot.messages (outreach_id, direction, sender, text, source)"
                 " VALUES (%s, 'out', 'bot', %s, 'bot')", (outreach_id, text))


def searches_since(conn: psycopg.Connection, device_id: Optional[str], since: datetime) -> int:
    """How many shops this device (None = any device) searched for since `since`.
    LINE limits searches whatever the result, so not-found searches count too."""
    return conn.execute(
        "SELECT count(*) FROM line_bot.outreach WHERE searched_at >= %(since)s"
        " AND (%(device)s::text IS NULL OR device_id = %(device)s::text)",
        {"device": device_id, "since": since},
    ).fetchone()[0]


def touch_device(conn: psycopg.Connection, device_id: str) -> None:
    conn.execute("INSERT INTO line_bot.devices (device_id, last_seen_at) VALUES (%s, now())"
                 " ON CONFLICT (device_id) DO UPDATE SET last_seen_at = now()", (device_id,))


def device_paused_until(conn: psycopg.Connection, device_id: str) -> Optional[datetime]:
    row = conn.execute("SELECT paused_until FROM line_bot.devices WHERE device_id = %s", (device_id,)).fetchone()
    return row[0] if row else None


def pause_device(conn: psycopg.Connection, device_id: str, until: datetime, status: str = "RESTRICTED") -> None:
    conn.execute("UPDATE line_bot.devices SET paused_until = %s, status = %s WHERE device_id = %s",
                 (until, status, device_id))


def queue_summary(conn: psycopg.Connection) -> Dict[str, int]:
    return dict(conn.execute("SELECT status, count(*) FROM line_bot.outreach GROUP BY status").fetchall())


# ---- replies ----

@dataclass(frozen=True)
class Contacted:
    id: int
    code: str
    shop_name: str
    status: str
    first_reply_at: Optional[datetime]
    last_reply_text: Optional[str]
    do_not_contact: bool


_CONTACTED_COLUMNS = "id, code, shop_name, status, first_reply_at, last_reply_text, do_not_contact"


def contacted_shop(conn: psycopg.Connection, outreach_id: int) -> Optional[Contacted]:
    """A shop the bot has already messaged (replies from shops never messaged are ignored)."""
    row = conn.execute(f"SELECT {_CONTACTED_COLUMNS} FROM line_bot.outreach"
                       " WHERE id = %s AND messaged_at IS NOT NULL", (outreach_id,)).fetchone()
    return Contacted(*row) if row else None


def record_incoming(conn: psycopg.Connection, outreach_id: int, text: str, source: str,
                    line_message_id: Optional[str]) -> bool:
    """Store a shop's message; False if this LINE message was stored before."""
    return conn.execute(
        "INSERT INTO line_bot.messages (outreach_id, direction, sender, text, source, line_message_id)"
        " VALUES (%s, 'in', 'shop', %s, %s, %s) ON CONFLICT (line_message_id) DO NOTHING",
        (outreach_id, text, source, line_message_id)).rowcount == 1


def pending_replies(conn: psycopg.Connection, only_phones: Optional[Collection[str]]) -> List[Tuple[Contacted, str]]:
    """Shops the bot still has to answer, with the message id to send (e.g. "B1")."""
    rows = conn.execute(
        f"SELECT {_CONTACTED_COLUMNS}, pending_reply FROM line_bot.outreach WHERE pending_reply IS NOT NULL"
        " AND (%(only)s::text[] IS NULL OR phone = ANY(%(only)s::text[])) ORDER BY last_reply_at, id",
        {"only": sorted(only_phones) if only_phones is not None else None}).fetchall()
    return [(Contacted(*row[:-1]), row[-1]) for row in rows]


def waiting_for_staff(conn: psycopg.Connection) -> List[tuple]:
    """(code, shop name, status, last reply, last reply time) for shops whose replies need a person."""
    return conn.execute(
        "SELECT code, shop_name, status, last_reply_text, last_reply_at FROM line_bot.outreach"
        " WHERE needs_human ORDER BY last_reply_at DESC NULLS LAST").fetchall()


def mark_no_reply(conn: psycopg.Connection, messaged_before: datetime) -> int:
    """Shops that never answered the opener by `messaged_before` become NO_REPLY (the bot does not follow up)."""
    return conn.execute("UPDATE line_bot.outreach SET status = 'NO_REPLY', updated_at = now()"
                        " WHERE status = 'MESSAGED' AND first_reply_at IS NULL AND messaged_at < %s",
                        (messaged_before,)).rowcount
