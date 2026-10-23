"""What the dashboard's control pages do: start and stop the bot, phone connection, team numbers, test shops."""

import os
import signal
import subprocess
import sys
from datetime import datetime
from typing import List, Optional, Sequence, Tuple

import psycopg

from . import db
from .config import ROOT, set_env_values, test_phones
from .phone import normalize_phone
from .status import CONSOLE_LOG, mark_stopped


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


def start_bot(*args: str, command: Optional[List[str]] = None) -> subprocess.Popen:
    """Start `python -m bot run` on its own, so it keeps running when the dashboard is closed or restarted.

    The dashboard follows it through logs/bot-status.json; its console output goes to logs/bot-console.log.
    """
    CONSOLE_LOG.parent.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        detach = {"creationflags": subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP}
    else:
        detach = {"start_new_session": True}
    with open(CONSOLE_LOG, "a", encoding="utf-8") as console:
        console.write(f"\n--- {datetime.now():%Y-%m-%d %H:%M:%S} เริ่มบอทจากหน้าเว็บ ---\n")
        console.flush()
        return subprocess.Popen(command or bot_command("run", *args), cwd=ROOT, env=_child_env(),
                                stdin=subprocess.DEVNULL, stdout=console, stderr=subprocess.STDOUT, **detach)


def force_stop(pid: int) -> None:
    """Kill a bot that does not answer the stop request (on Windows this is TerminateProcess)."""
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        pass  # already gone
    mark_stopped("บังคับหยุดจากหน้าเว็บ")


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
