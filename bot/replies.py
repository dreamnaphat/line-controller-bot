"""Notice shop replies (notifications first, the chat list as a fallback), record them, and answer once."""

import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Callable, Dict, Optional, Set, Tuple

from . import db
from .chat_list import parse_chat_list
from .classifier import ReplyClassifier
from .line_app import LineApp, LineUIError
from .notifications import outreach_id_from_title, parse_line_notifications
from .schedule import within
from .templates import TemplateError, render, shop_display_name

# Replies that end the conversation politely; the bot may send these even after its one pitch.
CLOSING_GROUPS = {"complaint", "wrong_number", "refuse", "closed"}
FINISHED = {"DECLINED", "CLOSED"}


@dataclass(frozen=True)
class ReplyEvent:
    outreach_id: int
    title: str
    text: str
    source: str                         # notification | chat_scan
    line_message_id: Optional[str] = None
    chat_id: Optional[str] = None


def _same_text(stored: str, seen: str) -> bool:
    """The chat list shows a shortened preview, so a stored reply that starts with it counts as the same."""
    stored, seen = re.sub(r"\s+", " ", stored).strip(), re.sub(r"\s+", " ", seen).strip()
    trimmed = seen.rstrip(".… ").strip()
    return stored == seen or (bool(trimmed) and stored.startswith(trimmed))


class ReplyHandler:
    """Database side: decide what a reply means for the shop. No screen work."""

    def __init__(self, conn, classifier: ReplyClassifier, auto_reply: bool,
                 alert: Callable[[str], None], log: Callable[[str], None], now: Callable[[], datetime]):
        self.conn, self.classifier, self.auto_reply = conn, classifier, auto_reply
        self.alert, self.log, self.now = alert, log, now

    def handle(self, event: ReplyEvent) -> Optional[str]:
        """Record the reply and return what happens next, or None if it was ignored or already known."""
        shop = db.contacted_shop(self.conn, event.outreach_id)
        if shop is None:
            return None
        if event.source == "chat_scan" and shop.last_reply_text and _same_text(shop.last_reply_text, event.text):
            return None
        if not db.record_incoming(self.conn, shop.id, event.text, event.source, event.line_message_id):
            return None

        now = self.now()
        result = self.classifier.classify(event.text)
        fields: Dict[str, Any] = {"last_reply_at": now, "last_reply_text": event.text}
        if event.chat_id:
            fields["line_chat_id"] = event.chat_id
        if result.do_not_contact:
            fields["do_not_contact"] = True

        if shop.first_reply_at is None:
            fields.update(first_reply_at=now, reply_group=result.group, shop_state=result.shop_state)
            if self.auto_reply:
                fields.update(status="REPLIED", pending_reply=result.reply, needs_human=result.alert)
                plan = f"บอทจะตอบ {result.reply}"
            else:
                fields.update(status=result.status if result.status in FINISHED else "REPLIED", needs_human=True)
                plan = "รอพนักงานตอบ"
        elif self.auto_reply and result.group in CLOSING_GROUPS and shop.status not in FINISHED:
            fields.update(pending_reply=result.reply, reply_group=result.group)
            plan = f"ร้านปิดบทสนทนา บอทจะตอบ {result.reply}"
        else:
            fields["needs_human"] = True
            plan = "รอพนักงานตอบ"

        db.update_outreach(self.conn, shop.id, **fields)
        self.log(f"{event.title}: ร้านตอบ \"{event.text}\" ({result.group}) — {plan}")
        if fields.get("needs_human"):
            self.alert(f"💬 {event.title} ตอบว่า: {event.text}\n→ {plan}")
        return plan


class ReplyWatcher:
    """Phone side: read notifications, scan the chat list now and then, and send the bot's pending replies.

    Notifications stay on the phone until someone opens the chat, so the same ones are read every round.
    What was already handled is remembered here, so a quiet round touches only the phone, never the database,
    and Neon can suspend while nothing happens.
    """

    def __init__(self, handler: ReplyHandler, app: LineApp, device, messages: Dict[str, str],
                 business: Dict[str, Any], allowed_phones: Optional[Set[str]], reply_window: Tuple[Any, Any],
                 scan_every_minutes: float, log: Callable[[str], None], now: Callable[[], datetime],
                 no_reply_days: int = 7):
        self.handler, self.app, self.d = handler, app, device
        self.messages, self.business, self.allowed = messages, business, allowed_phones
        self.window, self.scan_every = reply_window, scan_every_minutes * 60
        self.log, self.now, self.no_reply_days = log, now, no_reply_days
        self.last_scan: Optional[datetime] = None
        self.handled_notices: Set[str] = set()                 # message ids already handled
        self.scanned_rows: Dict[int, Tuple[str, int]] = {}     # outreach id -> (preview, unread) last handled
        self.no_reply_checked: Optional[date] = None
        self.pending_check = True  # replies left from an earlier run, or recorded outside reply hours

    def poll_notifications(self) -> int:
        """Read LINE notifications (no screen work). Chats without one of our codes are skipped unread."""
        dump = self.d.shell("dumpsys notification --noredact").output
        handled, on_screen = 0, set()
        for notice in parse_line_notifications(dump):
            outreach_id = outreach_id_from_title(notice.title)
            if outreach_id is None:
                continue
            key = notice.message_id or f"{notice.title}\n{notice.text}"
            on_screen.add(key)
            if key in self.handled_notices:
                continue
            event = ReplyEvent(outreach_id, notice.title, notice.text, "notification", notice.message_id,
                               notice.chat_id)
            handled += self._handle(event)
            self.handled_notices.add(key)
        self.handled_notices &= on_screen  # forget notifications that are gone
        return handled

    def _handle(self, event: ReplyEvent) -> bool:
        if self.handler.handle(event) is None:
            return False
        if self.handler.auto_reply:
            self.pending_check = True
        return True

    def scan_chat_list(self) -> int:
        """Open the Chats tab and record chats with unread messages that notifications missed."""
        self.last_scan = self.now()
        self.app.go_home()
        self.app.open_chat_tab()
        handled, unread = 0, {}
        for row in parse_chat_list(self.d.dump_hierarchy()):
            if row.unread <= 0:
                continue
            unread[row.outreach_id] = (row.preview, row.unread)
            if self.scanned_rows.get(row.outreach_id) != unread[row.outreach_id]:
                handled += self._handle(ReplyEvent(row.outreach_id, row.title, row.preview, "chat_scan"))
        self.scanned_rows = unread
        return handled

    def send_pending(self) -> int:
        sent = 0
        for shop, reply_id in db.pending_replies(self.handler.conn, self.allowed):
            try:
                text = render(self.messages[reply_id], shop=shop_display_name(shop.shop_name),
                              factory=self.business.get("factory_name"), persona=self.business.get("persona_name"),
                              source=self.business.get("source_text"))
                self.app.open_chat_with(shop.code)
                if not self.app.send_message(text):
                    raise LineUIError("กดส่งแล้วแต่ยืนยันไม่ได้ว่าข้อความออกไป")
            except (TemplateError, LineUIError) as exc:
                db.update_outreach(self.handler.conn, shop.id, pending_reply=None, needs_human=True)
                self.handler.alert(f"⚠️ {shop.code} {shop.shop_name}: บอทตอบ {reply_id} ไม่สำเร็จ ({exc}) ให้พนักงานตอบแทน")
                continue
            db.record_outgoing(self.handler.conn, shop.id, text)
            status = self.handler.classifier.status_after(reply_id)
            fields: Dict[str, Any] = {"status": status, "pending_reply": None}
            if status == "PITCHED":
                fields["pitched_at"] = self.now()
            db.update_outreach(self.handler.conn, shop.id, **fields)
            self.log(f"{shop.code} {shop.shop_name}: บอทตอบ {reply_id} แล้ว")
            sent += 1
        return sent

    def tick(self) -> None:
        """One round: read notifications; during reply hours also send pending replies and scan when due."""
        self.poll_notifications()
        now = self.now()
        if self.no_reply_checked != now.date():  # once a day is plenty for a 7-day limit
            self.no_reply_checked = now.date()
            expired = db.mark_no_reply(self.handler.conn, now - timedelta(days=self.no_reply_days))
            if expired:
                self.log(f"ร้านที่ไม่ตอบเกิน {self.no_reply_days} วัน: {expired} ร้าน → NO_REPLY")
        if not within(now, self.window):
            return
        used_screen = False
        if self.last_scan is None or (now - self.last_scan).total_seconds() >= self.scan_every:
            self.scan_chat_list()
            used_screen = True
        if self.pending_check:
            self.pending_check = False
            used_screen = self.send_pending() > 0 or used_screen
        if used_screen:
            self.app.leave()  # LINE in the background so new messages show up as notifications
