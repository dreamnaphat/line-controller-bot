"""The bot page: is the bot working, what is it doing, and where every shop of a monthly batch stands.

The live part reads logs/bot-status.json and logs/worker.log every few seconds and never the database;
the shop lists query the database only when the page is opened or refreshed, so Neon can still sleep.
"""

from datetime import date, datetime, timedelta
from html import escape
from typing import Optional

import streamlit as st

from bot import db, reports
from bot.config import device_serial, load_settings, set_env_values, test_phones
from bot.control import adb_connect, adb_devices, adb_pair, force_stop, start_bot
from bot.quota import QuotaRule, describe_plan, today_plan
from bot.reports import PHONE_PROBLEMS, STATUS_LABELS
from bot.safety import test_mode
from bot.schedule import parse_window, start_of_day
from bot.status import (CONSOLE_LOG, WORKER_LOG, read_status, request_stop, running, stop_requested_at, tail)
from dashboard.common import connection, fmt, html, phone_text, rim

THAI_MONTHS = ["ม.ค.", "ก.พ.", "มี.ค.", "เม.ย.", "พ.ค.", "มิ.ย.", "ก.ค.", "ส.ค.", "ก.ย.", "ต.ค.", "พ.ย.", "ธ.ค."]


def flash(message: str) -> None:
    st.session_state["flash"] = message
    st.rerun()


def batch_label(batch: str) -> str:
    """"2026-10" → "ต.ค. 2569" (Buddhist year, as staff write it)."""
    year, month = (int(part) for part in batch.split("-"))
    return f"{THAI_MONTHS[month - 1]} {year + 543}"


def when(moment: Optional[str]) -> str:
    if not moment:
        return "-"
    at = datetime.fromisoformat(moment).astimezone()
    today = date.today()
    if at.date() == today:
        return f"{at:%H:%M}"
    if at.date() == today + timedelta(days=1):
        return f"พรุ่งนี้ {at:%H:%M}"
    return f"{at:%d/%m %H:%M}"


# ---- live status (local files only) ----

def headline(status, alive: bool) -> str:
    if status is None:
        return '<span class="badge plain">ยังไม่เคยเริ่ม</span> กดเริ่มบอทด้านล่าง'
    activity = escape(status.get("activity") or "")
    if not alive and status.get("state") != "stopped":
        return (f'<span class="badge wait">ไม่ตอบสนอง</span> บอทเงียบไปตั้งแต่ {when(status.get("heartbeat"))} '
                "— อาจปิดไปแล้วหรือค้าง ดูบันทึกด้านล่าง")
    if not alive:
        return f'<span class="badge wait">หยุดอยู่</span> {activity} ({when(status.get("stopped_at"))})'
    if status.get("state") == "starting":
        return f'<span class="badge good">กำลังเริ่ม</span> {activity}'
    if status.get("state") == "working":
        return f'<span class="badge good">กำลังทักร้าน</span> {activity}'
    return (f'<span class="badge good">ทำงานอยู่</span> {activity}'
            + (f' — รอถึง {when(status.get("until"))}' if status.get("until") else "")
            + " · ระหว่างนี้คอยตรวจคำตอบของร้าน")


def facts(status) -> str:
    if not status:
        return ""
    today = status.get("today") or {}
    parts = []
    if today.get("date") == date.today().isoformat():
        parts.append(f"ทักวันนี้ {today['used']}/{today['quota']} ร้าน")
    parts.append(f"ตรวจคำตอบล่าสุด {when(status.get('last_reply_check'))}")
    if status.get("last_result"):
        parts.append(f"ร้านล่าสุด: {escape(status['last_result'])}")
    parts.append(f"โหมด{escape(status.get('mode', '-'))}")
    if status.get("device"):
        parts.append(f"มือถือ {escape(status['device'])}")
    return f'<p class="facts">{" · ".join(parts)}</p>'


def controls(status, alive: bool, testing: bool, team) -> None:
    if alive:
        asked = stop_requested_at()
        if asked is None:
            if st.button("หยุดบอท", icon=":material/stop_circle:",
                         help="บอทจะทำร้านที่กำลังทักให้เสร็จก่อนแล้วจึงหยุด"):
                request_stop()
                st.rerun(scope="fragment")
        else:
            st.caption(f"ส่งคำสั่งหยุดแล้วเมื่อ {asked:%H:%M:%S} — บอทจะหยุดหลังทำร้านที่กำลังทักเสร็จ")
            if datetime.now().astimezone() - asked > timedelta(minutes=2) and st.button(
                    "บังคับหยุด", type="primary", icon=":material/dangerous:",
                    help="ใช้เมื่อบอทค้าง ไม่ยอมหยุดเอง"):
                force_stop(status["pid"])
                st.rerun(scope="fragment")
        return

    if not testing:
        allowed = st.checkbox("ยืนยันให้บอททักร้านจริงจากคิว")
    elif not team:
        st.warning("โหมดทดสอบ: ยังไม่มีเบอร์ทีม — เพิ่มที่หน้าเครื่องมือทดสอบก่อน", icon=":material/info:")
        allowed = False
    else:
        allowed = True
    ignore_hours = testing and st.checkbox("ไม่สนช่วงเวลาทักร้าน (ใช้ตอนทดสอบ)", value=True)
    if st.button("เริ่มบอท", type="primary", icon=":material/play_arrow:", disabled=not allowed,
                 help="บอทจะทำงานต่อเองแม้ปิดหน้าเว็บนี้ จนกว่าจะกดหยุด"):
        start_bot(*(["--now"] if ignore_hours else []))
        st.toast("กำลังเริ่มบอท…", icon=":material/play_arrow:")


@st.fragment(run_every=5)
def live(testing: bool, team) -> None:
    status = read_status()
    alive = running(status)
    rim("บอททักร้าน", headline(status, alive))
    html(facts(status))
    controls(status, alive, testing, team)
    st.code(tail(WORKER_LOG, 40) or "ยังไม่มีบันทึกการทำงาน", language=None, height=300, wrap_lines=True)
    if status and not alive and status.get("state") != "stopped":
        with st.expander("ข้อความจากโปรแกรมบอท (ดูสาเหตุที่บอทหยุด)"):
            st.code(tail(CONSOLE_LOG, 40) or "-", language=None, wrap_lines=True)


# ---- phone ----

def phone_panel() -> None:
    serial = device_serial()
    try:
        online = [s for s, state in adb_devices() if state == "device"]
        problem = None
    except Exception as exc:  # adb server cannot start
        online, problem = [], str(exc)
    connected = (serial in online) if serial else len(online) == 1
    label = f"มือถือ: เชื่อมต่ออยู่ ({serial or online[0]})" if connected else "มือถือ: ยังไม่ได้เชื่อมต่อ"
    with st.expander(label, expanded=not connected, icon=":material/smartphone:"):
        if problem:
            st.error(f"เรียก adb ไม่ได้: {problem}")
        with st.form("adb-connect", border=False):
            address = st.text_input("IP:พอร์ต (มือถือ → ตัวเลือกสำหรับนักพัฒนา → Wireless debugging)",
                                    value=serial or "", placeholder="192.168.1.109:39195")
            if st.form_submit_button("เชื่อมต่อ", icon=":material/link:"):
                ok, message = adb_connect(address)
                if ok:
                    set_env_values({"DEVICE_SERIAL": address.strip()})
                    flash(f"เชื่อมต่อ {address.strip()} แล้ว")
                st.error(f"เชื่อมต่อไม่ได้: {message} — ถ้าเพิ่งเปิดเครื่องหรือเปลี่ยน Wi-Fi ให้จับคู่ใหม่ด้านล่าง")
        st.caption("จับคู่ครั้งแรก / จับคู่ใหม่: บนมือถือกด \"Pair device with pairing code\" แล้วกรอก IP:พอร์ต "
                   "และรหัส 6 หลักที่ขึ้นในหน้าต่างนั้น (พอร์ตสำหรับจับคู่ไม่ใช่พอร์ตเดียวกับที่ใช้เชื่อมต่อ)")
        with st.form("adb-pair", border=False):
            left, right = st.columns(2)
            pair_address = left.text_input("IP:พอร์ต สำหรับจับคู่", placeholder="192.168.1.109:41009")
            code = right.text_input("รหัสจับคู่", max_chars=6)
            if st.form_submit_button("จับคู่"):
                ok, message = adb_pair(pair_address, code)
                if ok:
                    flash("จับคู่สำเร็จ — ใส่ IP:พอร์ต สำหรับเชื่อมต่อด้านบนแล้วกดเชื่อมต่อ")
                st.error(f"จับคู่ไม่ได้: {message}")
        if st.button("ตรวจการเชื่อมต่อทั้งหมด", icon=":material/stethoscope:"):
            from bot.device import run_doctor

            with st.spinner("กำลังตรวจ…"):
                checks = run_doctor(device_serial(), load_settings()["device"]["line_package"])
            for check in checks:
                icon = "✅" if check.ok else ("❌" if check.required else "➖")
                st.markdown(f"{icon} {check.label}" + (f" — {check.detail}" if check.detail else ""))


# ---- shops of a batch (database, on load only) ----

def not_queued_tab(conn, settings) -> None:
    import_cfg = settings.get("import", {})
    rows = reports.not_yet_queued(conn, import_cfg)
    if rows is None:
        st.info("ไม่พบตาราง Shop ในฐานข้อมูลนี้")
        return
    if import_cfg.get("auto"):
        st.caption("ร้านใหม่ในตาราง Shop ที่ยังไม่เข้าคิว — บอทดึงร้านที่พร้อมเข้าคิวเองตอนเริ่มช่วงทักร้านทุกวัน "
                   "ร้านที่ยังไม่มีเบอร์จะเข้าคิวเมื่อระบบสแกนเติมเบอร์ให้")
    else:
        st.caption("ร้านใหม่ในตาราง Shop ที่ยังไม่เข้าคิว — การดึงอัตโนมัติปิดอยู่ (import.auto ใน config/settings.yaml) "
                   "ร้านจะเข้าคิวเมื่อรัน python -m bot import --commit")
    st.dataframe([{
        "ร้าน": row.shop_name, "เบอร์ใน Shop": row.phone_raw or "", "เปิดมาแล้ว (วัน)": row.age_days,
        "ผล": "พร้อมเข้าคิว" if row.status == "PENDING" else PHONE_PROBLEMS.get(row.invalid_reason, row.invalid_reason),
    } for row in rows], hide_index=True)


def queued_tab(conn, chosen, settings, team, testing) -> None:
    queue = reports.queued(chosen)
    rule = QuotaRule.from_config(settings["outreach"])
    now = datetime.now().astimezone()
    allowed = set(team) if testing else None
    plan = today_plan(conn, rule, allowed, now)
    used = db.searches_since(conn, None, start_of_day(now))
    session_end = parse_window(settings["outreach"]["outreach_hours"])[1]
    contactable = [s for s in queue if allowed is None or s["phone"] in allowed]
    days = dict(zip((s["id"] for s in contactable),
                    reports.expected_days(len(contactable), plan.quota, used, now.date(), now.time() < session_end)))
    st.caption(describe_plan(plan, rule) + " · วันที่คาดว่าจะทักเป็นค่าประมาณ บอทคิดโควตาใหม่ทุกวัน"
               + (" · โหมดทดสอบ: ทักเฉพาะร้านที่ใช้เบอร์ทีม" if testing else ""))
    st.dataframe([{
        "ลำดับ": n, "คาดว่าทัก": fmt(days[s["id"]]) if s["id"] in days else "ไม่ทัก (โหมดทดสอบ)",
        "รหัส": s["code"], "ร้าน": s["shop_name"], "เบอร์": phone_text(s["phone"]),
        "เปิดมาแล้ว (วัน)": s["age_days"], "สถานะ": STATUS_LABELS.get(s["status"], s["status"]),
    } for n, s in enumerate(queue, 1)], hide_index=True)


def contacted_tab(chosen) -> None:
    st.dataframe([{
        "รหัส": s["code"], "ร้าน": s["shop_name"], "เบอร์": phone_text(s["phone"]), "ทักเมื่อ": fmt(s["messaged_at"]),
        "สถานะ": STATUS_LABELS.get(s["status"], s["status"]), "คำตอบล่าสุด": s["last_reply_text"] or "",
    } for s in reports.contacted(chosen)], hide_index=True)


def not_contacted_tab(chosen) -> None:
    st.caption("ค้นใน LINE ไม่เจอ (ร้านไม่ได้เปิดให้ค้นด้วยเบอร์), เบอร์ใช้ไม่ได้, ทำไม่สำเร็จ "
               "หรือข้ามเพราะเซลส์จัดการร้านไปแล้ว — ร้านเหล่านี้บอทจะไม่ทักอีก")
    st.dataframe([{
        "รหัส": s["code"], "ร้าน": s["shop_name"], "เบอร์": phone_text(s["phone"]),
        "สถานะ": STATUS_LABELS.get(s["status"], s["status"]), "เหตุผล": reports.why_not_contacted(s),
    } for s in reports.not_contacted(chosen)], hide_index=True)


def shops_section(settings, testing: bool, team) -> None:
    conn = connection()
    if not db.schema_exists(conn):
        st.error("ยังไม่มีตารางของบอท — กดเริ่มบอทครั้งแรก หรือรัน python -m bot init-db")
        return
    head, refresh = st.columns([4, 1], vertical_alignment="bottom")
    shops = reports.load_shops(conn, include_test=testing)
    batches = sorted({reports.batch_of(s) for s in shops}, reverse=True) or [f"{date.today():%Y-%m}"]
    batch = head.selectbox("ร้านรอบเดือน (เดือนที่เข้าคิว)", batches, format_func=batch_label)
    if refresh.button("โหลดรายชื่อใหม่", icon=":material/refresh:", width="stretch"):
        st.rerun()
    chosen = [s for s in shops if reports.batch_of(s) == batch]
    total, done = len(chosen), len(reports.contacted(chosen))
    st.progress(done / total if total else 0.0, text=f"รอบ {batch_label(batch)}: ทักแล้ว {done} จาก {total} ร้าน")

    waiting = reports.queued(chosen)
    skipped = reports.not_contacted(chosen)
    tabs = st.tabs([":material/inbox: ยังไม่เข้าคิว", f":material/schedule: รอทัก ({len(waiting)})",
                    f":material/send: ทักแล้ว ({done})", f":material/block: ทักไม่ได้ / ข้าม ({len(skipped)})"])
    with tabs[0]:
        not_queued_tab(conn, settings)
    with tabs[1]:
        queued_tab(conn, chosen, settings, team, testing)
    with tabs[2]:
        contacted_tab(chosen)
    with tabs[3]:
        not_contacted_tab(chosen)


def render() -> None:
    settings = load_settings()
    testing = test_mode(settings)
    team = sorted(test_phones())
    live(testing, team)
    phone_panel()
    with st.container(border=True, key="panel-shops"):
        shops_section(settings, testing, team)
