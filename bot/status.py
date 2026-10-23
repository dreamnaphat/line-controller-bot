"""What the bot is doing right now, kept in a small local file the dashboard reads every few seconds.

The dashboard shows the bot live from this file instead of asking the database, so watching the bot does not
keep Neon awake. A stop request travels the other way as a file the bot looks for while it waits.
"""

import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from .device import LOG_DIR

STATUS_FILE = LOG_DIR / "bot-status.json"
STOP_FILE = LOG_DIR / "bot-stop"
WORKER_LOG = LOG_DIR / "worker.log"
CONSOLE_LOG = LOG_DIR / "bot-console.log"   # output of a bot started from the dashboard (tracebacks end up here)
STALE_AFTER = timedelta(minutes=3)          # one shop can take a minute or two without a heartbeat


def _now() -> datetime:
    return datetime.now().astimezone()


class StatusBoard:
    """Written by the running bot: state, what it is doing, today's progress and a heartbeat."""

    def __init__(self, path: Optional[Path] = None, now: Callable[[], datetime] = _now, **fields: Any):
        self.path, self.now = path or STATUS_FILE, now
        self.data: Dict[str, Any] = {"pid": os.getpid(), "started_at": now().isoformat(), "state": "starting",
                                     "activity": "กำลังเชื่อมต่อมือถือและฐานข้อมูล", **fields}
        self.update()

    def update(self, **fields: Any) -> None:
        self.data.update({key: value.isoformat() if isinstance(value, datetime) else value
                          for key, value in fields.items()})
        self.data["heartbeat"] = self.now().isoformat()
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.data, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, self.path)  # the dashboard never reads a half-written file
        except OSError:
            pass  # e.g. Windows refuses the swap while the dashboard is reading; the next update writes it again

    def stopped(self, reason: str) -> None:
        self.update(state="stopped", activity=reason, until=None, stopped_at=self.now())


def read_status(path: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    try:
        return json.loads((path or STATUS_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def running(status: Optional[Dict[str, Any]], now: Optional[datetime] = None) -> bool:
    """True while a bot is alive: not marked stopped and its heartbeat is recent (a crashed bot goes stale)."""
    if not status or status.get("state") == "stopped":
        return False
    heartbeat = datetime.fromisoformat(status["heartbeat"])
    return (now or _now()) - heartbeat < STALE_AFTER


def mark_stopped(reason: str, path: Optional[Path] = None) -> None:
    """Record that a bot which can no longer write its own status (killed) has stopped."""
    path = path or STATUS_FILE
    status = read_status(path)
    if status is None:
        return
    now = _now().isoformat()
    status.update(state="stopped", activity=reason, until=None, stopped_at=now, heartbeat=now)
    path.write_text(json.dumps(status, ensure_ascii=False), encoding="utf-8")


def stop_requested() -> bool:
    return STOP_FILE.exists()


def stop_requested_at() -> Optional[datetime]:
    try:
        return datetime.fromtimestamp(STOP_FILE.stat().st_mtime).astimezone()
    except OSError:
        return None


def request_stop() -> None:
    STOP_FILE.parent.mkdir(parents=True, exist_ok=True)
    STOP_FILE.touch()


def clear_stop_request() -> None:
    STOP_FILE.unlink(missing_ok=True)


def tail(path: Path, lines: int = 80) -> str:
    """The last lines of a log file, reading only its end."""
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            f.seek(max(0, f.tell() - 64 * 1024))
            text = f.read().decode("utf-8", errors="replace")
    except OSError:
        return ""
    return "\n".join(text.splitlines()[-lines:])
