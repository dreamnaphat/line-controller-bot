"""The control page's helpers (.env editing, the bot process, test shops) and the page itself."""

import os
import sys
import time
from datetime import datetime, timedelta, timezone

import pytest

from bot import config, control
from bot.control import add_team_phone, remove_team_phone
from bot.status import StatusBoard, mark_stopped, read_status, running, tail

TEST_URL = os.environ.get("TEST_DATABASE_URL")
needs_db = pytest.mark.skipif(not TEST_URL, reason="TEST_DATABASE_URL not set")


@pytest.fixture
def env_file(tmp_path, monkeypatch):
    path = tmp_path / ".env"
    path.write_text("# คอมเมนต์\nDATABASE_URL=postgresql://x\nTEST_PHONES=0811111111\nDEVICE_SERIAL=\n", encoding="utf-8")
    monkeypatch.setattr(config, "ENV_FILE", path)
    for key in ("TEST_PHONES", "DEVICE_SERIAL"):
        monkeypatch.delenv(key, raising=False)
    return path


def test_env_values_are_replaced_in_place_and_applied(env_file):
    config.set_env_values({"DEVICE_SERIAL": "192.168.1.109:39195", "NEW_KEY": "1"})
    assert env_file.read_text(encoding="utf-8").splitlines() == [
        "# คอมเมนต์", "DATABASE_URL=postgresql://x", "TEST_PHONES=0811111111", "DEVICE_SERIAL=192.168.1.109:39195",
        "NEW_KEY=1"]
    assert config.device_serial() == "192.168.1.109:39195"
    os.environ.pop("NEW_KEY")


def test_team_phones_are_normalized_and_kept_sorted(env_file):
    assert add_team_phone("+66 96-684-1124") == "0966841124"
    assert config.test_phones() == {"0811111111", "0966841124"}
    assert "TEST_PHONES=0811111111,0966841124" in env_file.read_text(encoding="utf-8")
    with pytest.raises(ValueError):
        add_team_phone("02-123-4567")  # landline
    remove_team_phone("0811111111")
    assert config.test_phones() == {"0966841124"}


def test_status_board_tells_the_dashboard_whether_the_bot_is_alive(tmp_path):
    clock = {"now": datetime(2026, 10, 23, 14, 0, tzinfo=timezone.utc)}
    path = tmp_path / "bot-status.json"
    board = StatusBoard(path, now=lambda: clock["now"], mode="ทดสอบ")
    board.update(state="waiting", activity="ครบโควตาวันนี้แล้ว (5/5)", until=clock["now"] + timedelta(hours=24))
    status = read_status(path)
    assert (status["state"], status["mode"], status["pid"]) == ("waiting", "ทดสอบ", os.getpid())
    assert running(status, clock["now"] + timedelta(minutes=1))
    assert not running(status, clock["now"] + timedelta(minutes=10))  # heartbeat went stale: the bot died
    board.stopped("หยุดตามคำสั่งจากหน้าเว็บ")
    assert not running(read_status(path), clock["now"])
    assert read_status(tmp_path / "missing.json") is None


def test_mark_stopped_and_tail(tmp_path):
    path = tmp_path / "bot-status.json"
    StatusBoard(path)
    mark_stopped("บังคับหยุดจากหน้าเว็บ", path)
    assert read_status(path)["activity"] == "บังคับหยุดจากหน้าเว็บ"
    log = tmp_path / "worker.log"
    log.write_text("\n".join(f"บรรทัด {n}" for n in range(500)), encoding="utf-8")
    assert tail(log, 3) == "บรรทัด 497\nบรรทัด 498\nบรรทัด 499"
    assert tail(tmp_path / "none.log") == ""


def test_start_bot_runs_on_its_own(tmp_path, monkeypatch):
    monkeypatch.setattr(control, "CONSOLE_LOG", tmp_path / "console.log")
    process = control.start_bot(command=[sys.executable, "-c", "import time\nprint('เริ่มแล้ว', flush=True)\ntime.sleep(60)"])
    try:
        for _ in range(50):
            if "เริ่มแล้ว" in (tmp_path / "console.log").read_text(encoding="utf-8"):
                break
            time.sleep(0.1)
        assert process.poll() is None
        assert "เริ่มบอทจากหน้าเว็บ" in (tmp_path / "console.log").read_text(encoding="utf-8")
    finally:
        process.kill()
        process.wait()


@needs_db
def test_test_page_adds_team_phone_and_test_shop(env_file, monkeypatch):
    psycopg = pytest.importorskip("psycopg")
    testing = pytest.importorskip("streamlit.testing.v1")
    from bot import db

    monkeypatch.setenv("DATABASE_URL", TEST_URL)
    monkeypatch.delenv("DASHBOARD_PASSWORD", raising=False)
    with psycopg.connect(TEST_URL, autocommit=True) as conn:
        conn.execute("DROP SCHEMA IF EXISTS line_bot CASCADE")
        db.apply_schema(conn)
        try:
            page = testing.AppTest.from_string(
                "from dashboard import test_page\ntest_page.render()", default_timeout=30).run()
            assert not page.exception
            assert "1 เบอร์" in page.markdown[0].value

            team_input = next(t for t in page.text_input if t.label == "เพิ่มเบอร์ทีม")
            team_input.input("0966841124")
            next(b for b in page.button if b.label == "เพิ่มเบอร์").click().run()
            assert not page.exception
            assert config.test_phones() == {"0811111111", "0966841124"}

            next(s for s in page.selectbox if s.label == "เบอร์ทีม").select("0966841124")
            next(t for t in page.text_input if t.label == "ชื่อร้านสมมติ").input("ร้านทดสอบ 1")
            next(b for b in page.button if b.label == "เพิ่มร้านทดสอบเข้าคิว").click().run()
            assert not page.exception
            assert conn.execute("SELECT code, shop_name, phone, status FROM line_bot.outreach").fetchall() == [
                ("C1", "ร้านทดสอบ 1", "0966841124", "PENDING")]
            assert any("เริ่มใหม่" == b.label for b in page.button)
        finally:
            conn.execute("DROP SCHEMA IF EXISTS line_bot CASCADE")
