"""What the dashboard's control page does: phone connection, team numbers, test shops, and running the bot."""

import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Callable, List, Optional, Sequence, Tuple

import psycopg

from . import db
from .config import ROOT, set_env_values, test_phones
from .device import LOG_DIR
from .phone import normalize_phone

BOT_LOG = LOG_DIR / "bot-ui.log"


def bot_command(*args: str) -> List[str]:
    return [sys.executable, "-m", "bot", *args]


def _child_env() -> dict:
    # UTF-8 so Thai survives the pipe on Windows; unbuffered so the log shows each line as it happens.
    return {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1"}


def run_command(*args: str, timeout: float = 600) -> Tuple[int, str]:
    """Run a short bot command (e.g. run --once --now) and return (exit code, output)."""
    try:
        result = subprocess.run(bot_command(*args), cwd=ROOT, env=_child_env(), capture_output=True,
                                text=True, encoding="utf-8", errors="replace", timeout=timeout)
    except subprocess.TimeoutExpired:
        return 1, f"ใช้เวลานานเกิน {timeout:.0f} วินาที — หยุดคำสั่งแล้ว"
    return result.returncode, (result.stdout + result.stderr).strip()


class BotRunner:
    """The long-running `python -m bot run` started from the dashboard; only one at a time."""

    def __init__(self, command: Callable[..., List[str]] = bot_command, log_path: Path = BOT_LOG):
        self.command, self.log_path = command, log_path
        self.process: Optional[subprocess.Popen] = None
        self.started_at: Optional[datetime] = None

    def running(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def start(self, *args: str) -> None:
        if self.running():
            raise RuntimeError("บอททำงานอยู่แล้ว")
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.log_path, "w", encoding="utf-8") as log:
            self.process = subprocess.Popen(self.command(*args), cwd=ROOT, env=_child_env(), stdout=log,
                                            stderr=subprocess.STDOUT)
        self.started_at = datetime.now()

    def stop(self) -> None:
        if not self.running():
            return
        self.process.terminate()  # every step is already saved in the database, so stopping mid-shop is safe
        try:
            self.process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.process.kill()
        with open(self.log_path, "a", encoding="utf-8") as log:
            log.write(f"{datetime.now():%Y-%m-%d %H:%M:%S}  หยุดบอทจากหน้า dashboard\n")

    def log_tail(self, lines: int = 60) -> str:
        if not self.log_path.exists():
            return ""
        return "\n".join(self.log_path.read_text(encoding="utf-8", errors="replace").splitlines()[-lines:])


# ---- phone ----

def adb_devices() -> List[Tuple[str, str]]:
    """(serial, state) of every phone adb can see."""
    import adbutils

    return [(d.serial, d.get_state()) for d in adbutils.adb.device_list()]


def adb_connect(address: str) -> Tuple[bool, str]:
    """Connect to a phone over Wireless debugging (the IP:port shown on the phone changes now and then)."""
    import adbutils

    try:
        message = adbutils.adb.connect(address.strip(), timeout=10)
    except Exception as exc:
        return False, str(exc)
    return "connected to" in message and "fail" not in message and "cannot" not in message, message


def adb_pair(address: str, code: str) -> Tuple[bool, str]:
    """First-time pairing with the code from "Pair device with pairing code" on the phone."""
    import adbutils

    try:
        result = subprocess.run([adbutils.adb_path(), "pair", address.strip(), code.strip()], capture_output=True,
                                text=True, encoding="utf-8", errors="replace", timeout=30)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, str(exc)
    output = (result.stdout + result.stderr).strip()
    return "Successfully paired" in output, output


# ---- team numbers and test shops ----

def add_team_phone(raw: str) -> str:
    """Add a number to TEST_PHONES in .env and return it normalized."""
    phone = normalize_phone(raw)
    if phone.phone is None:
        raise ValueError(f"เบอร์ {raw!r} ใช้ค้นใน LINE ไม่ได้ ({phone.reason})")
    set_env_values({"TEST_PHONES": ",".join(sorted(test_phones() | {phone.phone}))})
    return phone.phone


def remove_team_phone(phone: str) -> None:
    set_env_values({"TEST_PHONES": ",".join(sorted(test_phones() - {phone}))})


def add_test_shop(conn: psycopg.Connection, raw_phone: str, name: str, team: Sequence[str]) -> str:
    """Queue (or reset to PENDING) a fake shop for a team number; return its code."""
    phone = normalize_phone(raw_phone)
    if phone.phone is None:
        raise ValueError(f"เบอร์ {raw_phone!r} ใช้ค้นใน LINE ไม่ได้ ({phone.reason})")
    if phone.phone not in team:
        raise ValueError(f"เพิ่มได้เฉพาะเบอร์ทีมที่อยู่ใน TEST_PHONES ({phone.phone} ไม่อยู่ในรายการ)")
    try:
        return db.add_test_shop(conn, phone.phone, name.strip() or "ร้านทดสอบ")
    except psycopg.errors.UniqueViolation:
        if not conn.autocommit:
            conn.rollback()
        raise ValueError(f"เบอร์ {phone.phone} ซ้ำกับร้านจริงที่อยู่ในคิวแล้ว ใช้เบอร์ทีมเบอร์อื่นแทน") from None
