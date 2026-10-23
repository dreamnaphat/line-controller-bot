"""Work through the outreach queue on one phone: search, add, rename, send the opener, and record every step."""

import random
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Callable, Dict, Optional, Set, Tuple

from . import db
from .line_app import LineApp, LineUIError, SearchResult
from .quota import QuotaRule, describe_plan, today_plan
from .safety import blocked_reason
from .schedule import next_window_start, parse_window, start_of_day, within
from .templates import friend_label, render, shop_display_name


class Outcome(str, Enum):
    MESSAGED = "MESSAGED"
    NOT_FOUND = "NOT_FOUND"
    LIMITED = "LIMITED"      # LINE blocked phone search; nothing was changed for this shop
    FAILED = "FAILED"        # a screen step failed; the shop stays in the queue (ERROR after max attempts)
    BLOCKED = "BLOCKED"      # test mode refused this number


@dataclass(frozen=True)
class WorkerSettings:
    opener_id: str
    quota: QuotaRule
    window: Tuple[Any, Any]
    delay_range: Tuple[float, float]
    max_consecutive_not_found: int
    not_found_pause_minutes: int
    label_max_len: int = 20
    max_attempts: int = 3
    max_consecutive_failures: int = 3
    search_timeout: float = 15.0

    @classmethod
    def from_config(cls, settings: Dict[str, Any]) -> "WorkerSettings":
        outreach = settings["outreach"]
        return cls(
            opener_id=outreach["opener"],
            quota=QuotaRule.from_config(outreach),
            window=parse_window(outreach["outreach_hours"]),
            delay_range=tuple(outreach["delay_between_shops_sec"]),
            max_consecutive_not_found=int(outreach["max_consecutive_not_found"]),
            not_found_pause_minutes=int(outreach["not_found_pause_minutes"]),
            label_max_len=int(settings["device"]["friend_label_max_len"]),
        )


class Worker:
    def __init__(self, conn, app: LineApp, config: WorkerSettings, messages: Dict[str, str],
                 business: Dict[str, Any], device_id: str, settings: Dict[str, Any],
                 allowed_phones: Optional[Set[str]],
                 log: Callable[[str], None] = print,
                 on_error: Callable[[db.QueueItem], None] = lambda item: None,
                 now: Callable[[], datetime] = lambda: datetime.now().astimezone(),
                 sleep: Callable[[float], None] = time.sleep,
                 delay: Callable[[float, float], float] = random.uniform,
                 replies: Optional[Any] = None):
        """`conn` must be in autocommit mode so every step is saved the moment it happens.
        `allowed_phones` is the TEST_PHONES set while test mode is on, otherwise None."""
        self.conn, self.app, self.cfg = conn, app, config
        self.messages, self.business, self.settings = messages, business, settings
        self.device_id, self.allowed = device_id, allowed_phones
        self.log, self.on_error, self.now, self.sleep, self.delay = log, on_error, now, sleep, delay
        self.replies = replies  # a ReplyWatcher: checked between shops and while waiting
        self._quota_logged: Optional[Tuple[Any, int]] = None

    # ---- one shop ----

    def opener_for(self, item: db.QueueItem) -> str:
        return render(self.messages[self.cfg.opener_id], shop=shop_display_name(item.shop_name),
                      factory=self.business.get("factory_name"), persona=self.business.get("persona_name"),
                      source=self.business.get("source_text"))

    def process(self, item: db.QueueItem) -> Outcome:
        reason = blocked_reason(item.phone, self.settings, self.allowed or set())
        if reason:
            self.log(f"{item.code}: ข้าม — {reason.splitlines()[0]}")
            return Outcome.BLOCKED
        text = self.opener_for(item)
        try:
            self.app.go_home()
            result = self.app.search_phone(item.phone, timeout=self.cfg.search_timeout)
            if result == SearchResult.LIMITED:
                return Outcome.LIMITED
            db.update_outreach(self.conn, item.id, searched_at=self.now(), device_id=self.device_id)
            if result == SearchResult.NOT_FOUND:
                db.update_outreach(self.conn, item.id, status="NOT_FOUND")
                self.log(f"{item.code} {item.shop_name}: ค้นไม่เจอใน LINE")
                return Outcome.NOT_FOUND
            if result == SearchResult.UNKNOWN:
                raise LineUIError("ผลการค้นหาไม่ตรงกับแบบที่รู้จัก")

            line_name = self.app.result_name()
            if result == SearchResult.CAN_ADD:
                self.app.add_friend()
            if item.status == "PENDING":
                db.update_outreach(self.conn, item.id, status="ADDED", added_at=self.now(),
                                   line_name=line_name, device_id=self.device_id)

            self.app.open_chat()
            self.app.rename_friend(friend_label(item.code, item.shop_name, self.cfg.label_max_len))
            # Only a shop left half-done (ADDED) by an earlier run can already have the message.
            if item.status == "ADDED" and self.app.already_sent(text):
                self.log(f"{item.code} {item.shop_name}: ข้อความเคยส่งไปแล้วจากรอบก่อน ไม่ส่งซ้ำ")
            elif not self.app.send_message(text):
                raise LineUIError("กดส่งแล้วแต่ยืนยันไม่ได้ว่าข้อความออกไป")
            else:
                db.record_outgoing(self.conn, item.id, text)
                self.log(f"{item.code} {item.shop_name}: ส่งข้อความแล้ว")
            db.update_outreach(self.conn, item.id, status="MESSAGED", messaged_at=self.now(),
                               opener_variant=self.cfg.opener_id, device_id=self.device_id)
            return Outcome.MESSAGED
        except LineUIError as exc:
            self.on_error(item)
            status = db.record_failure(self.conn, item.id, str(exc), self.cfg.max_attempts)
            note = " — พักร้านนี้ไว้เป็น ERROR" if status == "ERROR" else ""
            self.log(f"{item.code} {item.shop_name}: ไม่สำเร็จ ({exc}){note}")
            return Outcome.FAILED

    # ---- the loop ----

    def _check_replies(self) -> None:
        if self.replies is not None:
            self.replies.tick()

    def quota_today(self, now: datetime) -> int:
        plan = today_plan(self.conn, self.cfg.quota, self.allowed, self.device_id, now)
        if self._quota_logged != (now.date(), plan.quota):
            self._quota_logged = (now.date(), plan.quota)
            self.log(describe_plan(plan, self.cfg.quota))
        return plan.quota

    def _wait_until(self, moment: datetime, why: str) -> None:
        self.log(f"{why} — รอถึง {moment:%d/%m %H:%M}" + (" (ระหว่างนี้คอยดูคำตอบของร้าน)" if self.replies else ""))
        self._check_replies()  # the outreach session just ended (or never started): catch up on replies first
        while True:
            remaining = (moment - self.now()).total_seconds()
            if remaining <= 0:
                return
            self.sleep(min(remaining, 30))
            self._check_replies()

    def run(self, once: bool = False, ignore_hours: bool = False, max_shops: Optional[int] = None) -> str:
        """Contact shops until the queue is empty, LINE limits searching, or `max_shops` is reached.

        Outside working hours, over the daily quota or while the phone is paused, the loop waits;
        with `once` it returns the reason instead of waiting.

        Replies are checked only while waiting, never between shops: notifications stay on the phone until
        read, so the session runs without interruption and the replies are handled right after it.
        """
        db.touch_device(self.conn, self.device_id)
        done = not_found_streak = failure_streak = 0
        idle_logged = False
        while True:
            now = self.now()
            paused_until = db.device_paused_until(self.conn, self.device_id)
            if paused_until is not None and paused_until > now:
                if once:
                    return f"มือถือถูกพักถึง {paused_until.astimezone(now.tzinfo):%d/%m %H:%M}"
                self._wait_until(paused_until, "มือถือถูกพัก")
                continue
            if not ignore_hours and not within(now, self.cfg.window):
                if once:
                    return "อยู่นอกเวลาทักร้าน"
                self._wait_until(next_window_start(now, self.cfg.window), "นอกเวลาทักร้าน")
                continue
            used = db.searches_since(self.conn, self.device_id, start_of_day(now))
            quota = self.quota_today(now)
            if used >= quota:
                if once:
                    return f"ครบโควตาวันนี้แล้ว ({used}/{quota})"
                self._wait_until(next_window_start(start_of_day(now) + timedelta(days=1), self.cfg.window),
                                 f"ครบโควตาวันนี้แล้ว ({used}/{quota})")
                continue

            item = db.next_in_queue(self.conn, self.allowed)
            if item is None:
                empty = "ไม่มีร้านเหลือในคิว" + (" (โหมดทดสอบ: ดูเฉพาะเบอร์ใน TEST_PHONES)" if self.allowed is not None else "")
                if self.replies is None or once:
                    return empty
                if not idle_logged:
                    self.log(f"{empty} — คอยดูคำตอบของร้านต่อ (กด Ctrl+C เพื่อหยุด)")
                    idle_logged = True
                self._check_replies()
                self.sleep(30)
                continue
            idle_logged = False

            outcome = self.process(item)
            done += 1
            if self.replies is not None:
                self.app.leave()  # LINE in the background so replies show up as notifications
            if outcome == Outcome.LIMITED:
                until = next_window_start(start_of_day(now) + timedelta(days=1), self.cfg.window)
                db.pause_device(self.conn, self.device_id, until)
                return f"LINE จำกัดการค้นหาชั่วคราว — พักมือถือถึง {until:%d/%m %H:%M}"

            not_found_streak = not_found_streak + 1 if outcome == Outcome.NOT_FOUND else 0
            failure_streak = failure_streak + 1 if outcome == Outcome.FAILED else 0
            if failure_streak >= self.cfg.max_consecutive_failures:
                return f"ไม่สำเร็จติดกัน {failure_streak} ร้าน — หยุดให้คนตรวจหน้าจอ (ดูไฟล์ใน logs/)"
            if max_shops is not None and done >= max_shops:
                return f"ครบจำนวนที่สั่ง ({done} ร้าน)"
            if once:
                return f"ทำไป 1 ร้าน: {outcome.value}"

            if not_found_streak >= self.cfg.max_consecutive_not_found:
                not_found_streak = 0
                self._wait_until(self.now() + timedelta(minutes=self.cfg.not_found_pause_minutes),
                                 f"ค้นไม่เจอติดกัน {self.cfg.max_consecutive_not_found} ร้าน พักกันโดนจำกัด")
            else:
                self.sleep(self.delay(*self.cfg.delay_range))
