"""Tell the sales team about replies that need a person — via Telegram when configured, else only the log."""

import json
import urllib.parse
import urllib.request
from typing import Callable, Optional


def make_alert(log: Callable[[str], None], token: Optional[str], chat_id: Optional[str]) -> Callable[[str], None]:
    def alert(text: str) -> None:
        log(f"[แจ้งเตือน] {text}")
        if not (token and chat_id):
            return
        data = urllib.parse.urlencode({"chat_id": chat_id, "text": text}).encode()
        try:
            with urllib.request.urlopen(f"https://api.telegram.org/bot{token}/sendMessage", data=data, timeout=10) as r:
                if not json.load(r).get("ok"):
                    log("ส่งแจ้งเตือน Telegram ไม่สำเร็จ")
        except Exception as exc:  # never stop the bot because an alert failed
            log(f"ส่งแจ้งเตือน Telegram ไม่สำเร็จ: {exc}")

    return alert
