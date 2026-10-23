"""The testing page: step-by-step commands, team phone numbers and fake shops, without the terminal or .env.

Starting, stopping and watching the bot is on the bot page.
"""

from datetime import datetime
from html import escape

import streamlit as st

from bot import db, reports
from bot.config import load_settings, test_phones
from bot.control import add_team_phone, add_test_shop, remove_team_phone, run_command
from bot.reports import STATUS_LABELS
from bot.safety import test_mode
from bot.status import read_status, running
from dashboard.common import BADGE_KIND, connection, fmt, phone_text, rim


def flash(message: str) -> None:
    st.session_state["flash"] = message
    st.rerun()


# ---- one step at a time ----

def _run_short(label: str, *args: str) -> None:
    with st.spinner(f"{label}… (ดูมือถือได้เลย)"):
        code, output = run_command(*args)
    st.session_state["last_command"] = (label, code, output, datetime.now())


def steps_section(testing: bool, team) -> None:
    st.subheader("สั่งทีละขั้น")
    if running(read_status()):
        st.info("บอทกำลังทำงานอยู่ — หยุดบอทที่หน้า \"บอท\" ก่อน (มือถือใช้ได้ทีละงาน)", icon=":material/info:")
        return
    if not testing:
        st.error("โหมดทดสอบปิดอยู่ — \"ทัก 1 ร้าน\" จะทักร้านจริงจากคิว", icon=":material/warning:")
        allowed = st.checkbox("เข้าใจแล้ว ให้ทักร้านจริงได้")
    else:
        allowed = bool(team)
    once, check = st.columns(2)
    if once.button("ทัก 1 ร้านตอนนี้", icon=":material/send:", disabled=not allowed, width="stretch",
                   help="ค้นเบอร์ → เพิ่มเพื่อน → เปลี่ยนชื่อ → ส่งข้อความ กับร้านถัดไปในคิว แล้วหยุด (ไม่สนช่วงเวลา)"):
        _run_short("กำลังทัก 1 ร้าน", "run", "--once", "--now")
    if check.button("ตรวจคำตอบตอนนี้", icon=":material/mark_chat_read:", width="stretch",
                    help="อ่านการแจ้งเตือนและหน้ารายการแชทของ LINE 1 รอบ"):
        _run_short("กำลังตรวจคำตอบ", "check-replies")
    if "last_command" in st.session_state:
        label, code, output, at = st.session_state["last_command"]
        result = "เสร็จ" if code == 0 else "ไม่สำเร็จ"
        st.caption(f"ผลล่าสุด: {label.replace('กำลัง', '')} — {result} ({at:%H:%M:%S})")
        st.code(output or "(ไม่มีข้อความ)", language=None)


# ---- team numbers ----

def team_section(team) -> None:
    st.subheader("เบอร์ทีมสำหรับทดสอบ")
    st.caption("ตอนโหมดทดสอบ บอททักได้เฉพาะเบอร์เหล่านี้")
    for phone in team:
        number, remove = st.columns([4, 1], vertical_alignment="center")
        number.markdown(phone_text(phone))
        if remove.button("ลบ", key=f"remove-{phone}", type="tertiary", icon=":material/close:"):
            remove_team_phone(phone)
            flash(f"ลบเบอร์ {phone_text(phone)} แล้ว")
    if not team:
        st.caption("ยังไม่มีเบอร์ทีม")
    with st.form("team-add", border=False, clear_on_submit=True):
        raw = st.text_input("เพิ่มเบอร์ทีม", placeholder="08x-xxx-xxxx")
        if st.form_submit_button("เพิ่มเบอร์", icon=":material/add:"):
            try:
                phone = add_team_phone(raw)
            except ValueError as exc:
                st.error(str(exc))
            else:
                flash(f"เพิ่มเบอร์ {phone_text(phone)} แล้ว")


# ---- test shops ----

def test_shops_section(team) -> None:
    st.subheader("ร้านทดสอบ")
    conn = connection()
    if not db.schema_exists(conn):
        st.error("ยังไม่มีตารางของบอท — รัน python -m bot init-db ก่อน")
        return
    shops = [s for s in reports.load_shops(conn, include_test=True) if reports.is_test(s)]
    st.caption("ร้านสมมติที่ใช้เบอร์ทีม บอทจะทักร้านเหล่านี้เหมือนร้านจริง — กด \"เริ่มใหม่\" เพื่อทดสอบซ้ำตั้งแต่ต้น")
    for shop in shops:
        info, again = st.columns([5, 1], vertical_alignment="center")
        status = shop["status"]
        reply = f' · ตอบว่า “{escape(shop["last_reply_text"])}”' if shop["last_reply_text"] else ""
        info.markdown(
            f'<div class="shop-head"><span class="code">{escape(shop["code"])}</span>'
            f'<span>{escape(shop["shop_name"])}</span></div>'
            f'<div class="facts">{phone_text(shop["phone"])} · '
            f'<span class="badge {BADGE_KIND.get(status, "plain")}">{escape(STATUS_LABELS.get(status, status))}</span>'
            f' · ทักเมื่อ {fmt(shop["messaged_at"])}{reply}</div>', unsafe_allow_html=True)
        if again.button("เริ่มใหม่", key=f"reset-{shop['id']}", icon=":material/restart_alt:",
                        disabled=shop["phone"] not in team, help="กลับเป็นรอทัก (ลบผลการทดสอบรอบก่อน)"):
            add_test_shop(conn, shop["phone"], shop["shop_name"], team)
            flash(f"{shop['code']} กลับเป็นรอทักแล้ว")
    if not shops:
        st.caption("ยังไม่มีร้านทดสอบ")

    add, clear = st.columns([3, 1], vertical_alignment="bottom")
    with add, st.form("test-shop-add", border=False, clear_on_submit=True):
        phone_col, name_col = st.columns(2)
        phone = phone_col.selectbox("เบอร์ทีม", team, format_func=phone_text, placeholder="เพิ่มเบอร์ทีมก่อน")
        name = name_col.text_input("ชื่อร้านสมมติ", placeholder="ร้านทดสอบ 1")
        if st.form_submit_button("เพิ่มร้านทดสอบเข้าคิว", icon=":material/add:", disabled=not team):
            try:
                code = add_test_shop(conn, phone, name, team)
            except ValueError as exc:
                st.error(str(exc))
            else:
                flash(f"เพิ่ม {code} เข้าคิวแล้ว")
    if shops:
        with clear.popover("ลบร้านทดสอบทั้งหมด", icon=":material/delete:"):
            st.caption("ลบร้านทดสอบและประวัติข้อความทั้งหมด (เพื่อนใน LINE ไม่ถูกลบ)")
            if st.button("ยืนยันลบ", type="primary"):
                removed = db.clear_test_shops(conn)
                flash(f"ลบร้านทดสอบแล้ว {removed} ร้าน")


def render() -> None:
    settings = load_settings()
    testing = test_mode(settings)
    team = sorted(test_phones())
    if testing:
        subtitle = f"โหมดทดสอบ — บอททักได้เฉพาะเบอร์ทีม <strong>{len(team)} เบอร์</strong>"
    else:
        subtitle = "<strong>โหมดทักร้านจริง</strong> — เปลี่ยนได้ที่ safety.test_mode ใน config/settings.yaml"
    rim("เครื่องมือทดสอบ", subtitle)

    with st.container(border=True, key="panel-steps"):
        steps_section(testing, team)
    left, right = st.columns([2, 3], gap="medium")
    with left, st.container(border=True, key="panel-team"):
        team_section(team)
    with right, st.container(border=True, key="panel-shops"):
        test_shops_section(team)
