"""The control page's helpers (.env editing, the bot process, test shops) and the page itself."""

import os
import sys
import time

import pytest

from bot import config
from bot.control import BotRunner, add_team_phone, remove_team_phone

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


def test_bot_runner_logs_and_stops(tmp_path):
    script = "import time\nprint('เริ่มแล้ว', flush=True)\ntime.sleep(60)"
    bot = BotRunner(command=lambda *args: [sys.executable, "-c", script], log_path=tmp_path / "bot.log")
    bot.start("run")
    with pytest.raises(RuntimeError):
        bot.start("run")
    for _ in range(50):
        if "เริ่มแล้ว" in bot.log_tail():
            break
        time.sleep(0.1)
    assert bot.running() and "เริ่มแล้ว" in bot.log_tail()
    bot.stop()
    assert not bot.running()
    assert bot.log_tail().endswith("หยุดบอทจากหน้า dashboard")


@needs_db
def test_control_page_adds_team_phone_and_test_shop(env_file, monkeypatch):
    psycopg = pytest.importorskip("psycopg")
    testing = pytest.importorskip("streamlit.testing.v1")
    from bot import db
    from dashboard import control_page

    monkeypatch.setenv("DATABASE_URL", TEST_URL)
    monkeypatch.delenv("DASHBOARD_PASSWORD", raising=False)
    monkeypatch.setattr(control_page, "adb_devices", lambda: [("192.168.1.109:39195", "device")])
    with psycopg.connect(TEST_URL, autocommit=True) as conn:
        conn.execute("DROP SCHEMA IF EXISTS line_bot CASCADE")
        db.apply_schema(conn)
        try:
            page = testing.AppTest.from_string(
                "from dashboard import control_page\ncontrol_page.render()", default_timeout=30).run()
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
