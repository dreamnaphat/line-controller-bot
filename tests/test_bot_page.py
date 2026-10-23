"""The bot page against a throwaway PostgreSQL (TEST_DATABASE_URL) and a fake bot status file."""

import os
from datetime import datetime, timedelta

import pytest

psycopg = pytest.importorskip("psycopg")
testing = pytest.importorskip("streamlit.testing.v1")

from bot import db, reports, status  # noqa: E402
from tests.shop_table import add_shop, create_shop_table  # noqa: E402

TEST_URL = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not TEST_URL, reason="TEST_DATABASE_URL not set")


@pytest.fixture
def conn(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", TEST_URL)
    monkeypatch.setenv("TEST_PHONES", "0966841124")
    monkeypatch.setenv("DEVICE_SERIAL", "192.168.1.109:39195")
    monkeypatch.delenv("DASHBOARD_PASSWORD", raising=False)
    with psycopg.connect(TEST_URL, autocommit=True) as c:
        c.execute("DROP SCHEMA IF EXISTS line_bot CASCADE")
        create_shop_table(c)
        db.apply_schema(c)
        yield c
        c.execute('DROP SCHEMA IF EXISTS line_bot CASCADE; DROP TABLE IF EXISTS "Shop"')


@pytest.fixture
def page_module(monkeypatch, bot_files):
    from dashboard import bot_page

    monkeypatch.setattr(bot_page, "adb_devices", lambda: [("192.168.1.109:39195", "device")])
    monkeypatch.setattr(bot_page, "WORKER_LOG", bot_files / "worker.log")
    monkeypatch.setattr(bot_page, "CONSOLE_LOG", bot_files / "bot-console.log")
    return bot_page


def open_page():
    return testing.AppTest.from_string("from dashboard import bot_page\nbot_page.render()", default_timeout=30).run()


def seed(conn):
    add_shop(conn, "new-in-shop", "0811111111", age=2)
    add_shop(conn, "no-phone", None, age=3)
    db.add_test_shop(conn, "0966841124", "ร้านทดสอบ 1")
    conn.execute("INSERT INTO line_bot.outreach (place_id, shop_name, phone, status, messaged_at)"
                 " VALUES ('TEST-0800000001', 'ร้านทักแล้ว', '0800000001', 'MESSAGED', now())")
    conn.execute("INSERT INTO line_bot.outreach (place_id, shop_name, phone, status, invalid_reason)"
                 " VALUES ('TEST-0800000002', 'ร้านเซลส์ไปแล้ว', '0800000002', 'SKIPPED', 'visited')")


def test_running_bot_with_its_shops(conn, page_module, bot_files):
    seed(conn)
    board = status.StatusBoard(mode="ทดสอบ", device="192.168.1.109:39195")
    board.update(state="waiting", activity="ครบโควตาวันนี้แล้ว (1/1)",
                 until=datetime.now().astimezone() + timedelta(hours=20), last_result="C9 ร้านเก่า: ส่งข้อความแล้ว")
    (bot_files / "worker.log").write_text("2026-10-23 14:05:00  C9 ร้านเก่า: ส่งข้อความแล้ว\n", encoding="utf-8")

    page = open_page()
    assert not page.exception
    text = "\n".join(m.value for m in page.markdown)
    assert "ทำงานอยู่" in text and "ครบโควตาวันนี้แล้ว (1/1)" in text and "C9 ร้านเก่า" in text
    assert "C9 ร้านเก่า: ส่งข้อความแล้ว" in page.code[0].value
    assert [t.label.split(" ", 1)[1] for t in page.tabs] == [
        "ยังไม่เข้าคิว", "รอทัก (1)", "ทักแล้ว (1)", "ทักไม่ได้ / ข้าม (1)"]
    not_queued = page.dataframe[0].value
    assert list(not_queued["ผล"]) == ["พร้อมเข้าคิว", "ไม่มีเบอร์"]
    assert page.dataframe[1].value["รหัส"].tolist() == ["C1"]
    assert page.dataframe[3].value["เหตุผล"].tolist() == ["เซลส์ไปเยี่ยมร้านแล้ว"]

    next(b for b in page.button if b.label == "หยุดบอท").click().run()
    assert status.STOP_FILE.exists()


def test_stopped_bot_can_be_started(conn, page_module, monkeypatch):
    started = []
    monkeypatch.setattr(page_module, "start_bot", lambda *args: started.append(args))
    status.StatusBoard(mode="ทดสอบ").stopped("หยุดตามคำสั่งจากหน้าเว็บ")

    page = open_page()
    assert not page.exception
    assert "หยุดอยู่" in "\n".join(m.value for m in page.markdown)
    next(b for b in page.button if b.label == "เริ่มบอท").click().run()
    assert started == [("--now",)]


def test_expected_days():
    today = datetime(2026, 10, 23).date()
    days = reports.expected_days(7, quota=3, used_today=1, today=today, today_open=True)
    assert [d.day for d in days] == [23, 23, 24, 24, 24, 25, 25]
    assert reports.expected_days(2, 3, 0, today, today_open=False)[0].day == 24
