"""Integration test against a throwaway PostgreSQL database.

Runs only when TEST_DATABASE_URL points to an empty scratch database (never Neon production):
    TEST_DATABASE_URL=postgresql://user:pass@localhost/scratch pytest tests/test_db.py
"""

import os

import pytest

psycopg = pytest.importorskip("psycopg")

from bot import db  # noqa: E402
from bot.importer import plan_import  # noqa: E402

TEST_URL = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not TEST_URL, reason="TEST_DATABASE_URL not set")

# Same definition as the production "Shop" table.
SHOP_DDL = """
CREATE TABLE "Shop" (
    "placeId" text PRIMARY KEY,
    "name" text NOT NULL,
    "estimatedOpeningDate" text,
    "calculatedAgeDays" integer,
    "analysisReason" text NOT NULL,
    "rating" double precision,
    "userRatingCount" integer NOT NULL,
    "phoneNumber" text,
    "latitude" double precision,
    "longitude" double precision,
    "googleMapsUrl" text NOT NULL,
    "province" text NOT NULL,
    "district" text NOT NULL,
    "foodType" text NOT NULL,
    "firstFoundAt" timestamp DEFAULT CURRENT_TIMESTAMP NOT NULL,
    "lastSeenAt" timestamp NOT NULL,
    "lastScanId" text NOT NULL,
    "status" text DEFAULT 'none' NOT NULL,
    "statusChangedAt" timestamp,
    "visited" boolean DEFAULT false NOT NULL,
    "visitedAt" timestamp,
    "visitNote" text,
    "saleId" integer,
    "saleName" text,
    "foundOnPage" integer
)
"""

IMPORT_CFG = {"shop_status": ["none"], "skip_visited": True, "food_types": [], "max_age_days": None}


@pytest.fixture
def conn():
    with psycopg.connect(TEST_URL) as c:
        c.execute('DROP SCHEMA IF EXISTS line_bot CASCADE; DROP TABLE IF EXISTS "Shop"')
        c.execute(SHOP_DDL)
        c.commit()
        db.apply_schema(c)
        yield c
        c.rollback()
        c.execute('DROP SCHEMA IF EXISTS line_bot CASCADE; DROP TABLE IF EXISTS "Shop"')
        c.commit()


def add_shop(conn, place_id, phone, age=None, status="none", visited=False, food="ก๋วยเตี๋ยว"):
    conn.execute(
        'INSERT INTO "Shop" ("placeId", "name", "calculatedAgeDays", "analysisReason", "userRatingCount",'
        ' "phoneNumber", "googleMapsUrl", "province", "district", "foodType", "lastSeenAt", "lastScanId",'
        ' "status", "visited")'
        " VALUES (%s, %s, %s, 'new', 3, %s, 'https://maps.google.com', 'กรุงเทพ', 'บางนา', %s, now(), 'scan-1', %s, %s)",
        (place_id, f"ร้าน {place_id}", age, phone, food, status, visited),
    )


def test_schema_is_idempotent(conn):
    db.apply_schema(conn)
    assert db.schema_exists(conn)


def test_import_flow_filters_orders_and_dedupes(conn):
    add_shop(conn, "old", "081 111 1111", age=90)
    add_shop(conn, "new", "+66 82 222 2222", age=5)
    add_shop(conn, "no-age", "083 333 3333", age=None)
    add_shop(conn, "landline", "02 123 4567", age=10)
    add_shop(conn, "visited", "084 444 4444", age=1, visited=True)
    add_shop(conn, "handled", "085 555 5555", age=1, status="contacted")
    add_shop(conn, "same-phone", "0822222222", age=6)

    shops = db.fetch_new_shops(conn, IMPORT_CFG)
    assert [s.place_id for s in shops] == ["new", "same-phone", "landline", "old", "no-age"]

    rows = plan_import(shops, db.fetch_existing_phones(conn))
    assert db.insert_outreach(conn, rows) == 5

    result = conn.execute(
        "SELECT place_id, code, status, phone, invalid_reason FROM line_bot.outreach ORDER BY id"
    ).fetchall()
    assert result == [
        ("new", "C1", "PENDING", "0822222222", None),
        ("same-phone", "C2", "INVALID", None, "duplicate"),
        ("landline", "C3", "INVALID", None, "landline"),
        ("old", "C4", "PENDING", "0811111111", None),
        ("no-age", "C5", "PENDING", "0833333333", None),
    ]

    # A second run finds nothing new.
    assert db.fetch_new_shops(conn, IMPORT_CFG) == []


def test_import_filters_by_food_type_and_age(conn):
    add_shop(conn, "noodle", "0811111111", age=10, food="ก๋วยเตี๋ยว")
    add_shop(conn, "cafe", "0822222222", age=10, food="คาเฟ่")
    add_shop(conn, "too-old", "0833333333", age=200, food="ก๋วยเตี๋ยว")

    cfg = dict(IMPORT_CFG, food_types=["ก๋วยเตี๋ยว"], max_age_days=60)
    assert [s.place_id for s in db.fetch_new_shops(conn, cfg)] == ["noodle"]

    cfg = dict(IMPORT_CFG, shop_status=[], skip_visited=False)
    assert len(db.fetch_new_shops(conn, cfg)) == 3


def test_invalid_status_is_rejected(conn):
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute("INSERT INTO line_bot.outreach (place_id, shop_name, status) VALUES ('x', 'x', 'WRONG')")
