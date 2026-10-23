"""Reply handling against a throwaway PostgreSQL (TEST_DATABASE_URL) and the fake LINE app."""

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

psycopg = pytest.importorskip("psycopg")

from bot import db  # noqa: E402
from bot.classifier import ReplyClassifier  # noqa: E402
from bot.line_app import LineApp  # noqa: E402
from bot.quota import QuotaRule  # noqa: E402
from bot.replies import ReplyEvent, ReplyHandler, ReplyWatcher  # noqa: E402
from bot.schedule import parse_window  # noqa: E402
from bot.templates import load_messages  # noqa: E402
from tests.fakes import PACKAGE, line_world, show_chat_in_list  # noqa: E402
from tests.shop_table import add_shop, create_shop_table  # noqa: E402

TEST_URL = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not TEST_URL, reason="TEST_DATABASE_URL not set")

DATA = Path(__file__).parent / "data"
BKK = timezone(timedelta(hours=7))
NOW = datetime(2026, 10, 23, 18, 30, tzinfo=BKK)
BUSINESS = {"factory_name": "ทดสอบ", "persona_name": "ต้น", "source_text": "Google Maps"}


@pytest.fixture
def conn():
    with psycopg.connect(TEST_URL, autocommit=True) as c:
        c.execute("DROP SCHEMA IF EXISTS line_bot CASCADE")
        create_shop_table(c)
        db.apply_schema(c)
        yield c
        c.execute('DROP SCHEMA IF EXISTS line_bot CASCADE; DROP TABLE IF EXISTS "Shop"')


def messaged_shop(conn, outreach_id, phone, name="ร้านทดสอบ"):
    conn.execute("INSERT INTO line_bot.outreach (id, place_id, shop_name, phone, status, messaged_at)"
                 " VALUES (%s, %s, %s, %s, 'MESSAGED', now())", (outreach_id, f"p{outreach_id}", name, phone))
    return f"C{outreach_id}"


def shop(conn, outreach_id):
    return conn.execute(
        "SELECT status, reply_group, shop_state, needs_human, do_not_contact, pending_reply, last_reply_text,"
        " line_chat_id, pitched_at IS NOT NULL FROM line_bot.outreach WHERE id = %s", (outreach_id,)).fetchone()


def setup(conn, auto_reply, window=("08:00", "20:00")):
    alerts, logs = [], []
    handler = ReplyHandler(conn, ReplyClassifier.from_config(), auto_reply, alerts.append, logs.append, lambda: NOW)
    device, selectors = line_world("friend")
    app = LineApp(device, selectors, PACKAGE, poll_interval=0.001)
    watcher = ReplyWatcher(handler, app, device, load_messages(), BUSINESS, None, parse_window(list(window)),
                           20, logs.append, lambda: NOW)
    return handler, watcher, device, selectors, alerts


def event(outreach_id, text, message_id=None, source="notification"):
    return ReplyEvent(outreach_id, f"C{outreach_id} ร้านทดสอบ", text, source, message_id, "uchat")


def test_without_auto_reply_every_reply_goes_to_staff(conn):
    messaged_shop(conn, 1, "0811111111")
    handler, _, _, _, alerts = setup(conn, auto_reply=False)

    assert handler.handle(event(1, "เปิดค่ะ", "m1")) == "รอพนักงานตอบ"
    assert shop(conn, 1) == ("REPLIED", "other", "OPEN", True, False, None, "เปิดค่ะ", "uchat", False)
    assert len(alerts) == 1 and "C1 ร้านทดสอบ" in alerts[0]
    assert handler.handle(event(1, "เปิดค่ะ", "m1")) is None  # same LINE message again


def test_without_auto_reply_refusals_are_still_marked(conn):
    messaged_shop(conn, 1, "0811111111")
    handler, *_ = setup(conn, auto_reply=False)
    handler.handle(event(1, "ไม่สนใจค่ะ", "m1"))
    status, group, _, needs_human, dnc, pending, *_ = shop(conn, 1)
    assert (status, group, needs_human, dnc, pending) == ("DECLINED", "refuse", True, True, None)


def test_auto_reply_pitches_once_then_hands_over(conn):
    code = messaged_shop(conn, 1, "0811111111", name="ก๋วยเตี๋ยวเรือป้าแดง")
    handler, watcher, device, selectors, alerts = setup(conn, auto_reply=True)
    show_chat_in_list(device, code)

    handler.handle(event(1, "เปิดค่ะ", "m1"))
    assert shop(conn, 1)[:6] == ("REPLIED", "other", "OPEN", False, False, "B1")
    assert alerts == []

    assert watcher.send_pending() == 1
    sent = device.sent_messages(selectors["chat_input"][0])
    assert len(sent) == 1 and "โรงงานลูกชิ้นทดสอบ" in sent[0]
    assert shop(conn, 1)[0] == "PITCHED" and shop(conn, 1)[5] is None and shop(conn, 1)[8] is True

    handler.handle(event(1, "สนใจครับ ส่งราคามาหน่อย", "m2"))
    assert shop(conn, 1)[3] is True and len(alerts) == 1  # a person takes over

    handler.handle(event(1, "เอาจริงไม่เอาแล้วครับ", "m3"))
    assert shop(conn, 1)[5] == "B5"
    watcher.send_pending()
    assert shop(conn, 1)[0] == "DECLINED"
    assert db.contacted_shop(conn, 1).do_not_contact


def test_auto_reply_closes_complaints_and_alerts(conn):
    code = messaged_shop(conn, 1, "0811111111")
    handler, watcher, device, selectors, alerts = setup(conn, auto_reply=True)
    show_chat_in_list(device, code)
    handler.handle(event(1, "ใครให้เบอร์มาครับ", "m1"))
    assert shop(conn, 1)[5] == "B4" and len(alerts) == 1
    watcher.send_pending()
    assert "Google Maps" in device.sent_messages(selectors["chat_input"][0])[0]
    assert shop(conn, 1)[0] == "DECLINED"


def test_failed_auto_reply_is_handed_to_staff(conn):
    messaged_shop(conn, 1, "0811111111")  # the chat is not in the chat list
    handler, watcher, _, _, alerts = setup(conn, auto_reply=True)
    handler.handle(event(1, "เปิดค่ะ", "m1"))
    assert watcher.send_pending() == 0
    status, *_rest = shop(conn, 1)
    assert status == "REPLIED" and shop(conn, 1)[3] is True and shop(conn, 1)[5] is None
    assert "ไม่สำเร็จ" in alerts[-1]


def test_replies_from_unknown_or_unmessaged_shops_are_ignored(conn):
    conn.execute("INSERT INTO line_bot.outreach (id, place_id, shop_name, phone) VALUES (5, 'p5', 'x', '0855555555')")
    handler, *_ = setup(conn, auto_reply=False)
    assert handler.handle(event(5, "สวัสดี", "m1")) is None
    assert handler.handle(event(99, "สวัสดี", "m2")) is None
    assert conn.execute("SELECT count(*) FROM line_bot.messages").fetchone()[0] == 0


def test_chat_scan_skips_a_reply_already_recorded(conn):
    messaged_shop(conn, 1, "0811111111")
    handler, *_ = setup(conn, auto_reply=False)
    handler.handle(event(1, "ดรีมกินข้าวยังครับ วันนี้", "m1"))
    assert handler.handle(event(1, "ดรีมกินข้าวยังครับ…", source="chat_scan")) is None
    assert handler.handle(event(1, "ขอเบอร์โทรหน่อย", source="chat_scan")) == "รอพนักงานตอบ"


def test_poll_notifications_reads_only_our_chats(conn):
    messaged_shop(conn, 0, "0800000000")
    messaged_shop(conn, 12, "0812121212", name="ร้านป้าแดง")
    _, watcher, device, _, _ = setup(conn, auto_reply=False)
    device.shell_output = (DATA / "line_notifications.txt").read_text(encoding="utf-8")

    assert watcher.poll_notifications() == 3
    texts = [r[0] for r in conn.execute("SELECT text FROM line_bot.messages ORDER BY id")]
    assert texts == ["ดรีมกินข้าวยัง", "ส่งสติกเกอร์", "เปิดค่ะ\nมีอะไรคะ"]
    assert watcher.poll_notifications() == 0  # same notifications still on screen


def test_scan_chat_list_records_unread_chats(conn):
    messaged_shop(conn, 0, "0800000000")
    messaged_shop(conn, 12, "0812121212")
    _, watcher, device, _, _ = setup(conn, auto_reply=False)
    device.hierarchy = (DATA / "chat_list.xml").read_text(encoding="utf-8")

    assert watcher.scan_chat_list() == 1  # C0 has 1 unread; C12 has none
    assert shop(conn, 0)[6] == "ดรีมก้ินข้าวยัง"
    assert shop(conn, 12)[0] == "MESSAGED"


class QueryCounter:
    """Wraps a connection and counts the statements sent through it."""

    def __init__(self, conn):
        self.conn, self.count = conn, 0

    def execute(self, *args, **kwargs):
        self.count += 1
        return self.conn.execute(*args, **kwargs)

    def __getattr__(self, name):
        return getattr(self.conn, name)


def test_quiet_rounds_never_touch_the_database(conn):
    """Neon suspends after 5 idle minutes; rounds that find nothing new must not wake it."""
    messaged_shop(conn, 0, "0800000000")
    counter = QueryCounter(conn)
    handler, watcher, device, _, _ = setup(counter, auto_reply=False)
    device.shell_output = (DATA / "line_notifications.txt").read_text(encoding="utf-8")
    device.hierarchy = (DATA / "chat_list.xml").read_text(encoding="utf-8")

    watcher.tick()  # first round: records the replies, sends nothing pending, marks old shops once
    assert shop(conn, 0)[0] == "REPLIED"
    first = counter.count
    assert first > 0

    watcher.tick()  # same notifications and the same unread chat as before
    watcher.last_scan = None
    watcher.tick()  # a chat-list scan that finds the same unread row
    assert counter.count == first


def test_a_new_message_in_the_same_chat_is_still_recorded(conn):
    messaged_shop(conn, 0, "0800000000")
    _, watcher, device, _, _ = setup(conn, auto_reply=False)
    device.shell_output = (DATA / "line_notifications.txt").read_text(encoding="utf-8")
    watcher.poll_notifications()
    device.shell_output = device.shell_output.replace("637404326209519999", "999").replace("ดรีมกินข้าวยัง", "พรุ่งนี้ว่าง")
    assert watcher.poll_notifications() == 1
    assert shop(conn, 0)[6] == "พรุ่งนี้ว่าง"


def test_tick_outside_reply_hours_only_reads_notifications(conn):
    messaged_shop(conn, 0, "0800000000")
    _, watcher, device, _, _ = setup(conn, auto_reply=False, window=("08:00", "09:00"))
    device.shell_output = (DATA / "line_notifications.txt").read_text(encoding="utf-8")
    watcher.tick()
    assert shop(conn, 0)[0] == "REPLIED"
    assert device.log == []  # no screen work


def test_worker_reads_replies_only_after_the_outreach_session(conn):
    from bot.worker import Worker, WorkerSettings

    messaged_shop(conn, 0, "0800000000")  # its reply is waiting in the notification shade
    for phone in ("0811111111", "0822222222"):
        add_shop(conn, f"p-{phone}", phone)
        conn.execute("INSERT INTO line_bot.outreach (place_id, shop_name, phone) VALUES (%s, 'ร้านใหม่', %s)",
                     (f"p-{phone}", phone))
    _, watcher, device, _, _ = setup(conn, auto_reply=False)
    device.shell_output = (DATA / "line_notifications.txt").read_text(encoding="utf-8")
    contacted_at_first_check = []
    real_tick = watcher.tick

    def tick():
        if not contacted_at_first_check:
            contacted_at_first_check.append(conn.execute(
                "SELECT count(*) FROM line_bot.outreach WHERE id > 0 AND status = 'MESSAGED'").fetchone()[0])
        real_tick()

    watcher.tick = tick
    clock = {"now": NOW.replace(hour=14, minute=0)}

    class Enough(Exception):
        pass

    def sleep(seconds):
        clock["now"] += timedelta(seconds=seconds)
        if clock["now"] > NOW.replace(hour=14, minute=30):
            raise Enough

    config = WorkerSettings(opener_id="Q2", quota=QuotaRule(fixed=5), window=parse_window(["14:00", "16:30"]),
                            delay_range=(60, 60), max_consecutive_not_found=5, not_found_pause_minutes=60,
                            search_timeout=0.05)
    worker = Worker(conn, watcher.app, config, load_messages(), BUSINESS, "phone-1", {"safety": {"test_mode": True}},
                    {"0800000000", "0811111111", "0822222222"}, log=lambda m: None, now=lambda: clock["now"],
                    sleep=sleep, delay=lambda a, b: a, replies=watcher)
    with pytest.raises(Enough):
        worker.run()

    assert contacted_at_first_check == [2]  # both new shops were contacted before any reply check
    assert shop(conn, 0)[0] == "REPLIED"


def test_worker_outside_the_session_checks_replies_while_waiting(conn):
    from bot.worker import Worker, WorkerSettings

    messaged_shop(conn, 0, "0800000000")
    _, watcher, device, _, _ = setup(conn, auto_reply=False)
    device.shell_output = (DATA / "line_notifications.txt").read_text(encoding="utf-8")

    class Enough(Exception):
        pass

    def sleep(seconds):
        raise Enough

    config = WorkerSettings(opener_id="Q2", quota=QuotaRule(fixed=5), window=parse_window(["14:00", "16:30"]),
                            delay_range=(0, 0), max_consecutive_not_found=5, not_found_pause_minutes=60)
    worker = Worker(conn, watcher.app, config, load_messages(), BUSINESS, "phone-1", {"safety": {"test_mode": True}},
                    {"0800000000"}, log=lambda m: None, now=lambda: NOW, sleep=sleep, replies=watcher)
    with pytest.raises(Enough):
        worker.run()  # 18:30: outside outreach hours, so it waits for tomorrow and reads replies first
    assert shop(conn, 0)[0] == "REPLIED"


def test_shops_silent_for_too_long_become_no_reply(conn):
    messaged_shop(conn, 1, "0811111111")
    messaged_shop(conn, 2, "0822222222")
    conn.execute("UPDATE line_bot.outreach SET messaged_at = %s WHERE id = 1", (NOW - timedelta(days=8),))
    conn.execute("UPDATE line_bot.outreach SET messaged_at = %s WHERE id = 2", (NOW - timedelta(days=2),))
    _, watcher, _, _, _ = setup(conn, auto_reply=False, window=("08:00", "09:00"))
    watcher.tick()
    assert shop(conn, 1)[0] == "NO_REPLY" and shop(conn, 2)[0] == "MESSAGED"
