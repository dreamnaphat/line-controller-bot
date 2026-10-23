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
from .reports import skip_label
from .safety import blocked_reason
from .schedule import next_window_start, parse_window, start_of_day, within
from .templates import friend_label, render, shop_display_name


class StopRequested(Exception):
    """Someone asked the bot to stop (the dashboard's stop button)."""


class Outcome(str, Enum):
    MESSAGED = "MESSAGED"
    NOT_FOUND = "NOT_FOUND"
    LIMITED = "LIMITED"      # LINE blocked phone search; nothing was changed for this shop
    FAILED = "FAILED"        # a screen step failed; the shop stays in the queue (ERROR after max attempts)
    BLOCKED = "BLOCKED"      # test mode refused this number
    SKIPPED = "SKIPPED"      # "Shop" changed after the shop was queued (visited by sales, status changed)


OUTCOME_LABELS = {"MESSAGED": "ส่งข้อความแล้ว", "NOT_FOUND": "ค้นไม่เจอใน LINE", "LIMITED": "LINE จำกัดการค้นหา",
                  "FAILED": "ไม่สำเร็จ", "BLOCKED": "ข้าม (โหมดทดสอบ)", "SKIPPED": "ข้าม (เซลส์จัดการแล้ว)"}


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
                 replies: Optional[Any] = None,
                 status: Optional[Any] = None,
                 should_stop: Callable[[], bool] = lambda: False):
        """`conn` must be in autocommit mode so every step is saved the moment it happens.
        `allowed_phones` is the TEST_PHONES set while test mode is on, otherwise None.
        `status` is a StatusBoard the dashboard watches; `should_stop` is polled while the bot waits."""
        self.conn, self.app, self.cfg = conn, app, config
        self.messages, self.business, self.settings = messages, business, settings
        self.device_id, self.allowed = device_id, allowed_phones
        self.log, self.on_error, self.now, self.sleep, self.delay = log, on_error, now, sleep, delay
        self.replies = replies  # a ReplyWatcher: checked while waiting, never during the outreach session
        self.status, self.should_stop = status, should_stop
        self._quota_logged: Optional[Tuple[Any, int]] = None
        self._imported_on: Optional[Any] = None

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
        skip = db.shop_skip_reason(self.conn, item.place_id, self.settings.get("import", {}))
        if skip:
            db.update_outreach(self.conn, item.id, status="SKIPPED", invalid_reason=skip)
            self.log(f"{item.code} {item.shop_name}: ไม่ทักแล้ว — {skip_label(skip)}")
            return Outcome.SKIPPED
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

    def _show(self, **fields: Any) -> None:
        if self.status is not None:
            self.status.update(**fields)

    def _pause(self, seconds: float) -> None:
        """Sleep in short steps, so a stop request is noticed within seconds and the heartbeat stays fresh."""
        while seconds > 0:
            step = min(seconds, 5)
            self.sleep(step)
            seconds -= step
            if self.should_stop():
                raise StopRequested
            self._show()

    def _check_replies(self) -> None:
        if self.replies is not None:
            self.replies.tick()
            self._show(last_reply_check=self.now())

    def import_new_shops(self, now: datetime) -> None:
        """Once a day, at the start of the session: queue the shops the monthly scan added to "Shop"."""
        import_cfg = self.settings.get("import", {})
        if not import_cfg.get("auto") or self._imported_on == now.date():
            return
        self._imported_on = now.date()
        found = db.import_new_shops(self.conn, import_cfg)
        queued, no_phone = found.get("PENDING", 0), found.get("INVALID:no_phone", 0)
        unusable = sum(found.values()) - queued - no_phone
        if not found:
            self.log("ดึงร้านใหม่จาก Shop: ไม่มีร้านใหม่")
            return
        self.log(f"ดึงร้านใหม่จาก Shop: เข้าคิว {queued} ร้าน"
                 + (f", ยังไม่มีเบอร์ {no_phone} ร้าน (เข้าคิวเมื่อมีเบอร์)" if no_phone else "")
                 + (f", เบอร์ใช้ไม่ได้ {unusable} ร้าน" if unusable else ""))

    def quota_today(self, now: datetime) -> int:
        plan = today_plan(self.conn, self.cfg.quota, self.allowed, now)
        if self._quota_logged != (now.date(), plan.quota):
            self._quota_logged = (now.date(), plan.quota)
            self.log(describe_plan(plan, self.cfg.quota))
        return plan.quota

    def _wait_until(self, moment: datetime, why: str, announce: bool = True) -> None:
        if announce:
            self.log(f"{why} — รอถึง {moment:%d/%m %H:%M}" + (" (ระหว่างนี้คอยดูคำตอบของร้าน)" if self.replies else ""))
        self._show(state="waiting", activity=why, until=moment)
        self._check_replies()  # the outreach session just ended (or never started): catch up on replies first
        while True:
            remaining = (moment - self.now()).total_seconds()
            if remaining <= 0:
                return
            self._pause(min(remaining, 30))
            self._check_replies()

    def run(self, once: bool = False, ignore_hours: bool = False, max_shops: Optional[int] = None) -> str:
        """Contact shops until the queue is empty, `max_shops` is reached or someone asks the bot to stop.

        Outside working hours, over the daily quota or while the phone is paused, the loop waits;
        with `once` it returns the reason instead of waiting.

        Replies are checked only while waiting, never between shops: notifications stay on the phone until
        read, so the session runs without interruption and the replies are handled right after it.
        """
        try:
            return self._run(once, ignore_hours, max_shops)
        except StopRequested:
            return "หยุดตามคำสั่งจากหน้าเว็บ"

    def _run(self, once: bool, ignore_hours: bool, max_shops: Optional[int]) -> str:
        db.touch_device(self.conn, self.device_id)
        done = not_found_streak = failure_streak = 0
        idle_logged = False
        while True:
            if self.should_stop():
                raise StopRequested
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
            self.import_new_shops(now)
            used = db.searches_since(self.conn, self.device_id, start_of_day(now))
            quota = self.quota_today(now)
            self._show(today={"date": now.date().isoformat(), "quota": quota, "used": used})
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
                # Nothing to contact until new shops arrive: wait for the next session instead of asking the
                # database every few seconds (every 15 minutes when ignoring the hours, e.g. while testing).
                later = now + timedelta(minutes=15) if ignore_hours else next_window_start(now, self.cfg.window)
                self._wait_until(later, empty, announce=not idle_logged)
                idle_logged = True
                continue
            idle_logged = False

            self._show(state="working", activity=f"กำลังทัก {item.code} {item.shop_name}", until=None)
            outcome = self.process(item)
            self._show(last_result=f"{item.code} {item.shop_name}: {OUTCOME_LABELS[outcome.value]}")
            if outcome == Outcome.SKIPPED:
                continue  # nothing happened on the phone; go straight to the next shop
            done += 1
            if self.replies is not None:
                self.app.leave()  # LINE in the background so replies show up as notifications
            if outcome == Outcome.LIMITED:
                until = next_window_start(start_of_day(now) + timedelta(days=1), self.cfg.window)
                db.pause_device(self.conn, self.device_id, until)
                limited = f"LINE จำกัดการค้นหาชั่วคราว — พักมือถือถึง {until:%d/%m %H:%M}"
                if once or self.replies is None:
                    return limited
                self.log(limited)
                continue  # the paused phone waits for the next session and keeps reading replies meanwhile

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
                self._show(activity="เว้นระยะก่อนทักร้านถัดไป")
                self._pause(self.delay(*self.cfg.delay_range))
