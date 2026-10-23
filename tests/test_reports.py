"""Dashboard data and the dashboard page itself, against a throwaway PostgreSQL (TEST_DATABASE_URL)."""

import os
from datetime import date, datetime, timedelta, timezone

import pytest

psycopg = pytest.importorskip("psycopg")

from bot import db, reports  # noqa: E402
from bot.config import ROOT  # noqa: E402

TEST_URL = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not TEST_URL, reason="TEST_DATABASE_URL not set")

BKK = timezone(timedelta(hours=7))
NOW = datetime(2026, 10, 23, 18, 30, tzinfo=BKK)


@pytest.fixture
def conn():
    with psycopg.connect(TEST_URL, autocommit=True) as c:
        c.execute("DROP SCHEMA IF EXISTS line_bot CASCADE")
        db.apply_schema(c)
        yield c
        c.execute("DROP SCHEMA IF EXISTS line_bot CASCADE")


def add(conn, place, phone, status="PENDING", **times):
    columns = ["place_id", "shop_name", "phone", "status", *times]
    values = [place, f"ร้าน {place}", phone, status, *times.values()]
    return conn.execute(f"INSERT INTO line_bot.outreach ({', '.join(columns)}) VALUES"
                        f" ({', '.join(['%s'] * len(values))}) RETURNING id", values).fetchone()[0]


def replied_shop(conn, place="p1", phone="0811111111"):
    outreach_id = add(conn, place, phone, "REPLIED", searched_at=NOW, added_at=NOW, messaged_at=NOW,
                      first_reply_at=NOW, last_reply_at=NOW)
    db.update_outreach(conn, outreach_id, needs_human=True, last_reply_text="เปิดแล้ว")
    db.record_outgoing(conn, outreach_id, "สวัสดีครับ ร้าน p1 เปิดขายอยู่ไหมครับ 🙏")
    db.record_incoming(conn, outreach_id, "เปิดแล้ว", "notification", "m1")
    return outreach_id


def test_funnel_counts_each_step(conn):
    add(conn, "a", None, "INVALID")
    add(conn, "b", "0800000001")
    add(conn, "c", "0800000002", "NOT_FOUND", searched_at=NOW)
    add(conn, "d", "0800000003", "MESSAGED", searched_at=NOW, added_at=NOW, messaged_at=NOW)
    replied_shop(conn, "e", "0800000004")
    add(conn, "f", "0800000005", "CUSTOMER", searched_at=NOW, added_at=NOW, messaged_at=NOW,
                   first_reply_at=NOW)

    steps = dict(reports.funnel(reports.load_shops(conn, include_test=False)))
    assert steps == {"นำเข้า": 6, "เบอร์มือถือใช้ได้": 5, "ค้นใน LINE แล้ว": 4, "เพิ่มเพื่อนได้": 3, "ทักแล้ว": 3,
                     "ร้านตอบ": 2, "สนใจ": 1, "เป็นลูกค้า": 1}


def test_test_shops_are_hidden_unless_asked(conn):
    add(conn, "real", "0800000001")
    db.add_test_shop(conn, "0966841124", "ร้านทดสอบ 1")
    assert [s["code"] for s in reports.load_shops(conn, include_test=False)] == ["C1"]
    assert len(reports.load_shops(conn, include_test=True)) == 2


def test_staff_marks_a_shop_interested(conn):
    outreach_id = replied_shop(conn)
    reports.save_staff_update(conn, outreach_id, "interested", "  ขอใบราคา ", None, answered=True,
                              do_not_contact=False)
    shop = reports.load_shops(conn, include_test=False)[0]
    assert (shop["status"], shop["needs_human"], shop["notes"]) == ("INTERESTED", False, "ขอใบราคา")
    assert reports.waiting_for_staff([shop]) == []


def test_not_open_yet_needs_a_date(conn):
    outreach_id = replied_shop(conn)
    with pytest.raises(ValueError):
        reports.save_staff_update(conn, outreach_id, "not_open_yet", "", None, True, False)
    reports.save_staff_update(conn, outreach_id, "not_open_yet", "", date(2026, 11, 15), True, False)
    shop = reports.load_shops(conn, include_test=False)[0]
    assert (shop["status"], shop["shop_state"], shop["recontact_on"]) == ("REPLIED", "NOT_OPEN_YET", date(2026, 11, 15))
    assert reports.recontacts([shop]) == [shop]


def test_declined_is_never_contacted_again(conn):
    outreach_id = replied_shop(conn)
    reports.save_staff_update(conn, outreach_id, "declined", "", None, answered=False, do_not_contact=False)
    shop = reports.load_shops(conn, include_test=False)[0]
    assert (shop["status"], shop["do_not_contact"], shop["needs_human"]) == ("DECLINED", True, True)


def test_conversation_history(conn):
    outreach_id = replied_shop(conn)
    assert [(d, s, t) for d, s, t, _ in reports.messages_of(conn, outreach_id)] == [
        ("out", "bot", "สวัสดีครับ ร้าน p1 เปิดขายอยู่ไหมครับ 🙏"), ("in", "shop", "เปิดแล้ว")]


def test_daily_activity(conn):
    replied_shop(conn)
    activity = reports.daily_activity(reports.load_shops(conn, False), NOW.date() - timedelta(days=1))
    assert activity == {NOW.astimezone().date(): {"ค้นเบอร์": 1, "ทักแล้ว": 1, "ร้านตอบ": 1}}


def test_dashboard_page_records_staff_outcome(conn, monkeypatch):
    testing = pytest.importorskip("streamlit.testing.v1")
    monkeypatch.setenv("DATABASE_URL", TEST_URL)
    monkeypatch.setenv("TEST_PHONES", "0966841124")
    monkeypatch.delenv("DASHBOARD_PASSWORD", raising=False)
    outreach_id = replied_shop(conn)
    add(conn, "queued", "0800000009")

    page = testing.AppTest.from_file(str(ROOT / "dashboard" / "app.py"), default_timeout=30).run()
    assert not page.exception
    assert [tab.label.split(" ", 1)[1] for tab in page.tabs] == ["รอพนักงาน (1)", "นัดทักใหม่ (0)", "ภาพรวม",
                                                                 "ร้านทั้งหมด"]
    assert any("ร้าน p1" in m.value and "เปิดแล้ว" in m.value for m in page.markdown)

    page.radio[0].set_value("interested")
    page.text_area[0].input("ขอใบราคา")
    page.button[0].click().run()  # the form's save button
    assert not page.exception
    status, needs_human, notes = conn.execute(
        "SELECT status, needs_human, notes FROM line_bot.outreach WHERE id = %s", (outreach_id,)).fetchone()
    assert (status, needs_human, notes) == ("INTERESTED", False, "ขอใบราคา")
    assert page.tabs[0].label.endswith("รอพนักงาน (0)")
    assert any("บันทึกผล C1" in t.value for t in page.toast)
