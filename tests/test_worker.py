"""Worker against a throwaway PostgreSQL (TEST_DATABASE_URL) and the fake LINE app from tests/fakes.py."""

import os
from datetime import datetime, timedelta, timezone

import pytest

psycopg = pytest.importorskip("psycopg")

from bot import db  # noqa: E402
from bot.line_app import LineApp  # noqa: E402
from bot.schedule import parse_window  # noqa: E402
from bot.worker import Worker, WorkerSettings  # noqa: E402
from tests.fakes import PACKAGE, line_world  # noqa: E402

TEST_URL = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not TEST_URL, reason="TEST_DATABASE_URL not set")

BKK = timezone(timedelta(hours=7))
IN_WINDOW = datetime(2026, 10, 21, 14, 30, tzinfo=BKK)
TEAM = "0966841124"
MESSAGES = {"Q2": "สวัสดีครับ {shop} เปิดขายอยู่ไหมครับ 🙏"}
TEST_MODE = {"safety": {"test_mode": True}}
CONFIG = WorkerSettings(opener_id="Q2", daily_quota=2, window=parse_window(["14:00", "16:30"]),
                        delay_range=(0, 0), max_consecutive_not_found=5, not_found_pause_minutes=60,
                        search_timeout=0.05)


@pytest.fixture
def conn():
    with psycopg.connect(TEST_URL, autocommit=True) as c:
        c.execute("DROP SCHEMA IF EXISTS line_bot CASCADE")
        db.apply_schema(c)
        yield c
        c.execute("DROP SCHEMA IF EXISTS line_bot CASCADE")


def queue(conn, phone, name="ก๋วยเตี๋ยวเรือป้าแดง - Boat Noodle", status="PENDING", place=None):
    return conn.execute(
        "INSERT INTO line_bot.outreach (place_id, shop_name, phone, status) VALUES (%s, %s, %s, %s)"
        " RETURNING id, code", (place or f"p-{phone}", name, phone, status)).fetchone()


def make_worker(conn, result="found", allowed=frozenset({TEAM}), settings=TEST_MODE, now=IN_WINDOW, config=CONFIG):
    device, selectors = line_world(result)
    app = LineApp(device, selectors, PACKAGE, poll_interval=0.001)
    logs = []
    worker = Worker(conn, app, config, MESSAGES, {}, "phone-1", settings,
                    set(allowed) if allowed is not None else None,
                    log=logs.append, now=lambda: now, sleep=lambda s: None, delay=lambda a, b: 0)
    return worker, device, selectors, logs


def row(conn, outreach_id):
    return conn.execute("SELECT status, line_name, opener_variant, device_id, attempts, last_error"
                        " FROM line_bot.outreach WHERE id = %s", (outreach_id,)).fetchone()


def test_new_shop_is_added_renamed_and_messaged(conn):
    outreach_id, code = queue(conn, TEAM)
    worker, device, selectors, _ = make_worker(conn, "found")

    assert worker.run(once=True).endswith("MESSAGED")

    status, line_name, variant, device_id, _, _ = row(conn, outreach_id)
    assert (status, line_name, variant, device_id) == ("MESSAGED", "Nong.  Nong", "Q2", "phone-1")
    name_input = selectors["name_edit_input"][0]
    assert ("type", name_input, f"{code} ก๋วยเตี๋ยวเรือป้าแดง"[:20]) in device.log
    text = "สวัสดีครับ ร้านก๋วยเตี๋ยวเรือป้าแดง เปิดขายอยู่ไหมครับ 🙏"
    assert device.sent_messages(selectors["chat_input"][0]) == [text]
    assert conn.execute("SELECT direction, sender, text FROM line_bot.messages").fetchall() == [("out", "bot", text)]


def test_existing_friend_skips_the_add_button(conn):
    outreach_id, _ = queue(conn, TEAM)
    worker, device, selectors, _ = make_worker(conn, "friend")
    worker.run(once=True)
    assert row(conn, outreach_id)[0] == "MESSAGED"
    assert ("click", selectors["result_add_button"][0]) not in device.log


def test_not_found_is_recorded(conn):
    outreach_id, _ = queue(conn, TEAM)
    worker, _, _, _ = make_worker(conn, "missing")
    assert worker.run(once=True).endswith("NOT_FOUND")
    assert row(conn, outreach_id)[0] == "NOT_FOUND"


def test_search_limit_pauses_the_phone_and_keeps_the_shop_queued(conn):
    outreach_id, _ = queue(conn, TEAM)
    worker, _, _, _ = make_worker(conn, "limited")
    assert "จำกัดการค้นหา" in worker.run()
    assert row(conn, outreach_id)[0] == "PENDING"
    paused = db.device_paused_until(conn, "phone-1")
    assert paused == datetime(2026, 10, 22, 14, 0, tzinfo=BKK)
    # The next run refuses to search until then.
    worker2, _, _, _ = make_worker(conn, "found")
    assert "พัก" in worker2.run(once=True)


def test_test_mode_never_touches_real_shops(conn):
    queue(conn, "0811111111", place="real")
    worker, device, _, _ = make_worker(conn, "found")
    assert "ไม่มีร้านเหลือในคิว" in worker.run(once=True)
    assert device.log == []


def test_failures_count_up_to_error(conn):
    outreach_id, _ = queue(conn, TEAM)
    for attempt in range(1, 4):
        worker, _, _, _ = make_worker(conn, "blank-result")
        worker.app.d.screens["blank-result"] = []
        assert worker.run(once=True).endswith("FAILED")
        assert row(conn, outreach_id)[4] == attempt
    assert row(conn, outreach_id)[0] == "ERROR"
    assert "ผลการค้นหา" in row(conn, outreach_id)[5]


def test_unfinished_shop_is_resumed_first_and_not_sent_twice(conn):
    queue(conn, TEAM, place="newer")
    resumed_id, _ = queue(conn, "0899999999", status="ADDED", place="resumed")
    worker, device, selectors, logs = make_worker(conn, "friend", allowed={TEAM, "0899999999"})
    opener = "สวัสดีครับ ร้านก๋วยเตี๋ยวเรือป้าแดง เปิดขายอยู่ไหมครับ 🙏"
    device.screens["chat"].append({"text": opener})  # the opener is already in the chat

    worker.run(once=True)

    assert row(conn, resumed_id)[0] == "MESSAGED"
    assert device.sent_messages(selectors["chat_input"][0]) == []
    assert any("ไม่ส่งซ้ำ" in line for line in logs)


def test_quota_counts_searches_including_not_found(conn):
    for phone in (TEAM, "0899999999", "0888888888"):
        queue(conn, phone)
    allowed = {TEAM, "0899999999", "0888888888"}
    worker, _, _, _ = make_worker(conn, "missing", allowed=allowed)
    assert worker.run(once=True).endswith("NOT_FOUND")
    assert worker.run(once=True).endswith("NOT_FOUND")
    assert db.searches_since(conn, "phone-1", IN_WINDOW.replace(hour=0, minute=0)) == 2
    assert "ครบโควตา" in worker.run(once=True)  # daily_quota is 2 in CONFIG


def test_outside_hours_does_nothing(conn):
    queue(conn, TEAM)
    worker, device, _, _ = make_worker(conn, now=IN_WINDOW.replace(hour=10))
    assert worker.run(once=True) == "อยู่นอกเวลาทักร้าน"
    assert worker.run(once=True, ignore_hours=True).endswith("MESSAGED")


def test_real_mode_contacts_any_queued_shop(conn):
    outreach_id, _ = queue(conn, "0811111111", place="real")
    worker, _, _, _ = make_worker(conn, allowed=None, settings={"safety": {"test_mode": False}})
    worker.run(once=True)
    assert row(conn, outreach_id)[0] == "MESSAGED"


def test_continuous_run_waits_for_the_window_then_works(conn):
    outreach_id, _ = queue(conn, TEAM)
    worker, _, _, logs = make_worker(conn)
    clock = {"now": IN_WINDOW.replace(hour=13, minute=0)}
    worker.now = lambda: clock["now"]
    worker.sleep = lambda seconds: clock.update(now=clock["now"] + timedelta(seconds=seconds))

    assert worker.run() == "ไม่มีร้านเหลือในคิว (โหมดทดสอบ: ดูเฉพาะเบอร์ใน TEST_PHONES)"
    assert row(conn, outreach_id)[0] == "MESSAGED"
    assert any("นอกเวลาทักร้าน" in line for line in logs)
    assert clock["now"] >= IN_WINDOW.replace(hour=14, minute=0)


def test_new_shop_is_sent_even_if_the_chat_has_a_similar_older_message(conn):
    """A chat can already hold an earlier test opener; only resumed (ADDED) shops check for it."""
    outreach_id, _ = queue(conn, TEAM)
    worker, device, selectors, logs = make_worker(conn, "friend")
    device.screens["chat"].append({"text": "สวัสดีครับ ร้านก๋วยเตี๋ยวเรือป้าแดง เปิดขายอยู่ไหมครับ 🙏"})
    worker.run(once=True)
    assert len(device.sent_messages(selectors["chat_input"][0])) == 1
    assert not any("ไม่ส่งซ้ำ" in line for line in logs)
