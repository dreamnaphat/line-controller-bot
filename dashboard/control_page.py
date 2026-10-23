"""The control page: everything a test run needs without the terminal or editing .env."""

from datetime import datetime
from html import escape

import streamlit as st

from bot import db, reports
from bot.config import device_serial, load_settings, set_env_values, test_phones
from bot.control import (BotRunner, add_team_phone, add_test_shop, adb_connect, adb_devices, adb_pair,
                         remove_team_phone, run_command)
from bot.reports import STATUS_LABELS
from bot.safety import test_mode
from dashboard.common import BADGE_KIND, connection, fmt, html, phone_text, rim


@st.cache_resource
def runner() -> BotRunner:
    """One bot process for the whole dashboard server, shared by every browser tab."""
    return BotRunner()


def flash(message: str) -> None:
    st.session_state["flash"] = message
    st.rerun()


# ---- bot ----

def _run_short(label: str, *args: str) -> None:
    with st.spinner(f"{label}… (ดูมือถือได้เลย)"):
        code, output = run_command(*args)
    st.session_state["last_command"] = (label, code, output, datetime.now())


@st.fragment(run_every=3)
def live_log(bot: BotRunner) -> None:
    if not bot.running():
        st.rerun()  # the bot stopped on its own: redraw the whole page
    st.code(bot.log_tail() or "รอบอทเริ่ม…", language=None, height=360)


def bot_section(bot: BotRunner, testing: bool, team, window) -> None:
    st.subheader("สั่งบอท")
    if not testing:
        st.error("โหมดทดสอบปิดอยู่ — บอทจะทักร้านจริงจากคิว", icon=":material/warning:")
        allowed = st.checkbox("เข้าใจแล้ว ให้บอททักร้านจริงได้")
    elif not team:
        st.warning("ยังไม่มีเบอร์ทีม — เพิ่มเบอร์ในหัวข้อ \"เบอร์ทีมสำหรับทดสอบ\" ก่อน", icon=":material/info:")
        allowed = False
    else:
        allowed = True

    if bot.running():
        html(f'<p class="facts"><span class="badge good">บอทกำลังทำงาน</span> เริ่มเมื่อ {bot.started_at:%H:%M} '
             "· บอทจะทักร้านตามคิวและคอยดูคำตอบไปเรื่อย ๆ</p>")
        if st.button("หยุดบอท", type="primary", icon=":material/stop_circle:"):
            bot.stop()
            flash("หยุดบอทแล้ว")
        live_log(bot)
        return

    once, check, start = st.columns(3)
    if once.button("ทัก 1 ร้านตอนนี้", icon=":material/send:", disabled=not allowed, width="stretch",
                   help="ค้นเบอร์ → เพิ่มเพื่อน → เปลี่ยนชื่อ → ส่งข้อความ กับร้านถัดไปในคิว แล้วหยุด"):
        _run_short("กำลังทัก 1 ร้าน", "run", "--once", "--now")
    if check.button("ตรวจคำตอบตอนนี้", icon=":material/mark_chat_read:", width="stretch",
                    help="อ่านการแจ้งเตือนและหน้ารายการแชทของ LINE 1 รอบ"):
        _run_short("กำลังตรวจคำตอบ", "check-replies")
    ignore_hours = st.checkbox(f"ไม่สนช่วงเวลาทักร้าน (ปกติทักเฉพาะ {window[0]}–{window[1]})", value=testing)
    if start.button("เริ่มทำงานต่อเนื่อง", type="primary", icon=":material/play_arrow:", disabled=not allowed,
                    width="stretch", help="ทักร้านตามคิวและโควตา และคอยดูคำตอบจนกว่าจะกดหยุด"):
        bot.start("run", *(["--now"] if ignore_hours else []))
        flash("เริ่มบอทแล้ว")

    if "last_command" in st.session_state:
        label, code, output, at = st.session_state["last_command"]
        status = "เสร็จ" if code == 0 else "ไม่สำเร็จ"
        st.caption(f"ผลล่าสุด: {label.replace('กำลัง', '')} — {status} ({at:%H:%M:%S})")
        st.code(output or "(ไม่มีข้อความ)", language=None)
    elif bot.log_tail():
        with st.expander("บันทึกการทำงานรอบล่าสุด"):
            st.code(bot.log_tail(), language=None)


# ---- phone ----

def phone_section() -> None:
    st.subheader("มือถือ")
    serial = device_serial()
    try:
        devices = adb_devices()
    except Exception as exc:  # adb server cannot start
        devices = []
        st.error(f"เรียก adb ไม่ได้: {exc}")
    online = [s for s, state in devices if state == "device"]
    if serial and serial in online:
        html(f'<p class="facts"><span class="badge good">เชื่อมต่ออยู่</span> {escape(serial)}</p>')
    elif not serial and len(online) == 1:
        html(f'<p class="facts"><span class="badge good">เชื่อมต่ออยู่</span> {escape(online[0])}</p>')
    else:
        html('<p class="facts"><span class="badge wait">ยังไม่ได้เชื่อมต่อ</span> '
             "เปิด Wireless debugging บนมือถือ แล้วใส่ IP:พอร์ต ที่เห็นบนหน้านั้น</p>")

    with st.form("adb-connect", border=False):
        address = st.text_input("IP:พอร์ต (มือถือ → ตัวเลือกสำหรับนักพัฒนา → Wireless debugging)",
                                value=serial or "", placeholder="192.168.1.109:39195")
        if st.form_submit_button("เชื่อมต่อ", icon=":material/link:"):
            ok, message = adb_connect(address)
            if ok:
                set_env_values({"DEVICE_SERIAL": address.strip()})
                flash(f"เชื่อมต่อ {address.strip()} แล้ว")
            st.error(f"เชื่อมต่อไม่ได้: {message} — ถ้าเพิ่งเปิดเครื่องหรือเปลี่ยน Wi-Fi ให้จับคู่ใหม่ด้านล่าง")

    with st.expander("จับคู่ครั้งแรก / จับคู่ใหม่"):
        st.caption("บนมือถือกด \"Pair device with pairing code\" แล้วกรอก IP:พอร์ต และรหัส 6 หลักที่ขึ้นในหน้าต่างนั้น "
                   "(พอร์ตสำหรับจับคู่ไม่ใช่พอร์ตเดียวกับที่ใช้เชื่อมต่อ)")
        with st.form("adb-pair", border=False):
            pair_address = st.text_input("IP:พอร์ต สำหรับจับคู่", placeholder="192.168.1.109:41009")
            code = st.text_input("รหัสจับคู่", max_chars=6)
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
    rim("ควบคุมการทดสอบ", subtitle)

    with st.container(border=True, key="panel-bot"):
        bot_section(runner(), testing, team, settings["outreach"]["outreach_hours"])
    left, right = st.columns(2, gap="medium")
    with left, st.container(border=True, key="panel-phone"):
        phone_section()
    with right, st.container(border=True, key="panel-team"):
        team_section(team)
    with st.container(border=True, key="panel-shops"):
        test_shops_section(team)
