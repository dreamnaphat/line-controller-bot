"""Staff dashboard (Streamlit). Start it with: python -m bot dashboard"""

import hmac
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import psycopg
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # Streamlit runs this file on its own

from bot import db, reports  # noqa: E402
from bot.config import dashboard_password, database_url, load_settings, test_phones  # noqa: E402
from bot.quota import QuotaRule, describe_plan, today_plan  # noqa: E402
from bot.reports import SHOP_STATE_LABELS, STAFF_OUTCOMES, STATUS_LABELS  # noqa: E402
from bot.safety import test_mode  # noqa: E402

st.set_page_config(page_title="LINE Bot ร้านก๋วยเตี๋ยว", page_icon="🍜", layout="wide")


def fmt(moment) -> str:
    if moment is None:
        return "-"
    if isinstance(moment, datetime):
        return f"{moment.astimezone():%d/%m/%y %H:%M}"
    return f"{moment:%d/%m/%y}"


@st.cache_resource
def _connection() -> psycopg.Connection:
    return db.connect(database_url(), autocommit=True)


def connection() -> psycopg.Connection:
    """The shared connection, reopened if Neon closed it while idle."""
    conn = _connection()
    try:
        conn.execute("SELECT 1")
    except psycopg.Error:
        _connection.clear()
        conn = _connection()
    return conn


def require_password() -> None:
    password = dashboard_password()
    if not password or st.session_state.get("signed_in"):
        return
    entered = st.text_input("รหัสผ่าน dashboard", type="password")
    if entered and hmac.compare_digest(entered, password):
        st.session_state["signed_in"] = True
        st.rerun()
    if entered:
        st.error("รหัสผ่านไม่ถูกต้อง")
    st.stop()


# ---- one shop: conversation + staff form ----

def _default_outcome(shop) -> int:
    by_status = {"INTERESTED": "interested", "CUSTOMER": "customer", "DECLINED": "declined", "CLOSED": "closed"}
    key = by_status.get(shop["status"]) or ("not_open_yet" if shop["shop_state"] == "NOT_OPEN_YET" else "talking")
    return list(STAFF_OUTCOMES).index(key)


def staff_form(conn, shop, key: str) -> None:
    with st.form(f"staff-{key}-{shop['id']}"):
        outcome = st.radio("ผลการคุย", list(STAFF_OUTCOMES), index=_default_outcome(shop),
                           format_func=lambda k: STAFF_OUTCOMES[k].label)
        recontact = st.date_input("นัดทักใหม่วันที่ (ไม่บังคับ ยกเว้นร้านยังไม่เปิด)", value=shop["recontact_on"],
                                  format="DD/MM/YYYY")
        notes = st.text_area("โน้ต", value=shop["notes"] or "", placeholder="เช่น ขอใบราคา, ใช้ลูกชิ้นเจ้าอื่นอยู่")
        do_not_contact = st.checkbox("ห้ามทักร้านนี้อีก", value=shop["do_not_contact"])
        answered = st.checkbox("ตอบร้านแล้ว (เอาออกจากรายการรอพนักงาน)", value=True)
        if st.form_submit_button("บันทึก", type="primary"):
            try:
                reports.save_staff_update(conn, shop["id"], outcome, notes, recontact, answered, do_not_contact)
            except ValueError as exc:
                st.error(str(exc))
                return
            st.session_state["flash"] = f"บันทึก {shop['code']} {shop['shop_name']} แล้ว"
            st.rerun()


def shop_detail(conn, shop, key: str) -> None:
    left, right = st.columns([3, 2], gap="large")
    with left:
        facts = [f"เบอร์ {shop['phone'] or '-'}", f"สถานะ: {STATUS_LABELS.get(shop['status'], shop['status'])}"]
        if shop["shop_state"]:
            facts.append(f"ร้าน: {SHOP_STATE_LABELS.get(shop['shop_state'], shop['shop_state'])}")
        if shop["line_name"]:
            facts.append(f"ชื่อใน LINE: {shop['line_name']}")
        if shop["do_not_contact"]:
            facts.append("⛔ ห้ามทัก")
        st.caption(" · ".join(facts))
        st.caption(f"คุยต่อใน LINE PC: ค้นหาแชทด้วยรหัส **{shop['code']}** "
                   "(ข้อความที่พนักงานพิมพ์ใน LINE จะไม่ขึ้นที่นี่)")
        history = reports.messages_of(conn, shop["id"])
        if not history:
            st.caption("ยังไม่มีข้อความ")
        for direction, sender, text, at in history:
            incoming = direction == "in"
            with st.chat_message("user" if incoming else "assistant", avatar="🏪" if incoming else "🤖"):
                st.write(text or "(ไม่มีข้อความ)")
                st.caption(fmt(at))
    with right:
        staff_form(conn, shop, key)


# ---- tabs ----

def waiting_tab(conn, shops) -> None:
    waiting = reports.waiting_for_staff(shops)
    if not waiting:
        st.success("ไม่มีร้านที่รอพนักงานตอบ")
        return
    st.caption("ร้านที่ตอบกลับมาและรอคนคุยต่อ — ตอบใน LINE PC แล้วบันทึกผลที่นี่")
    for shop in waiting:
        label = f"{shop['code']} {shop['shop_name']} — “{shop['last_reply_text'] or ''}” · {fmt(shop['last_reply_at'])}"
        with st.expander(label, expanded=len(waiting) <= 5):
            shop_detail(conn, shop, "waiting")


def recontact_tab(conn, shops) -> None:
    planned = reports.recontacts(shops)
    if not planned:
        st.info("ยังไม่มีร้านที่นัดทักใหม่ (ตั้งได้จากฟอร์มบันทึกผล)")
        return
    today = date.today()
    for shop in planned:
        due = shop["recontact_on"] <= today
        label = f"{'🔔 ' if due else ''}{fmt(shop['recontact_on'])} — {shop['code']} {shop['shop_name']}"
        if shop["notes"]:
            label += f" · {shop['notes'][:60]}"
        with st.expander(label, expanded=False):
            shop_detail(conn, shop, "recontact")


def overview_tab(conn, shops, allowed) -> None:
    by_status = {}
    for shop in shops:
        by_status[shop["status"]] = by_status.get(shop["status"], 0) + 1
    messaged = sum(1 for s in shops if s["messaged_at"])
    replied = sum(1 for s in shops if s["first_reply_at"])

    cols = st.columns(6)
    cols[0].metric("รอพนักงาน", sum(1 for s in shops if s["needs_human"]))
    cols[1].metric("รอทักในคิว", by_status.get("PENDING", 0) + by_status.get("ADDED", 0))
    cols[2].metric("ทักแล้ว", messaged)
    cols[3].metric("ร้านตอบ", replied, f"{replied / messaged:.0%} ของที่ทัก" if messaged else None, delta_color="off")
    cols[4].metric("สนใจ", by_status.get("INTERESTED", 0) + by_status.get("CUSTOMER", 0))
    cols[5].metric("เป็นลูกค้า", by_status.get("CUSTOMER", 0))

    rule = QuotaRule.from_config(load_settings()["outreach"])
    plan = today_plan(conn, rule, allowed, None, datetime.now().astimezone())
    st.info(describe_plan(plan, rule) + (" — โหมดทดสอบ: นับเฉพาะเบอร์ใน TEST_PHONES" if allowed is not None else ""))

    left, right = st.columns(2, gap="large")
    with left:
        st.subheader("Funnel ต่อรอบนำเข้า")
        batches = sorted({reports.batch_of(s) for s in shops}, reverse=True)
        batch = st.selectbox("รอบ (เดือนที่นำเข้า)", ["ทุกรอบ"] + batches)
        chosen = shops if batch == "ทุกรอบ" else [s for s in shops if reports.batch_of(s) == batch]
        steps = reports.funnel(chosen)
        total = steps[0][1] or 1
        for label, count in steps:
            st.progress(count / total, text=f"{label}: {count} ({count / total:.0%})")
    with right:
        st.subheader("30 วันล่าสุด")
        activity = reports.daily_activity(shops, date.today() - timedelta(days=29))
        if activity:
            rows = [{"วันที่": day.isoformat(), **counts} for day, counts in activity.items()]  # ISO sorts by date
            st.bar_chart(rows, x="วันที่", stack=False)
        else:
            st.caption("ยังไม่มีการทักใน 30 วันล่าสุด")
        st.subheader("จำนวนร้านตามสถานะ")
        st.dataframe([{"สถานะ": STATUS_LABELS.get(k, k), "รหัส": k, "จำนวน": v}
                      for k, v in sorted(by_status.items(), key=lambda item: -item[1])],
                     hide_index=True)


def all_shops_tab(conn, shops) -> None:
    search_col, status_col = st.columns([2, 3])
    query = search_col.text_input("ค้นหา (รหัส ชื่อร้าน หรือเบอร์)").strip().lower()
    statuses = status_col.multiselect("สถานะ", list(STATUS_LABELS), format_func=lambda k: STATUS_LABELS[k],
                                      placeholder="ทุกสถานะ")
    found = [s for s in shops
             if (not statuses or s["status"] in statuses)
             and (not query or query in f"{s['code']} {s['shop_name']} {s['phone'] or ''}".lower())]
    st.caption(f"{len(found)} ร้าน")
    st.dataframe([{
        "รหัส": s["code"], "ร้าน": s["shop_name"], "เบอร์": s["phone"] or "",
        "สถานะ": STATUS_LABELS.get(s["status"], s["status"]), "ทักเมื่อ": fmt(s["messaged_at"]),
        "คำตอบล่าสุด": s["last_reply_text"] or "", "นัดทักใหม่": fmt(s["recontact_on"]) if s["recontact_on"] else "",
        "โน้ต": s["notes"] or "",
    } for s in found], hide_index=True)
    by_code = {s["code"]: s for s in found}
    picked = st.selectbox("เปิดดู/บันทึกผลร้าน", [None] + list(by_code),
                          format_func=lambda code: "เลือกร้าน" if code is None else f"{code} {by_code[code]['shop_name']}")
    if picked is not None:
        shop_detail(conn, by_code[picked], "all")


def main() -> None:
    require_password()
    settings = load_settings()
    allowed = test_phones() if test_mode(settings) else None

    with st.sidebar:
        st.title("🍜 LINE Bot")
        include_test = st.toggle("แสดงร้านทดสอบ", value=allowed is not None)
        if allowed is not None:
            st.caption("โหมดทดสอบเปิดอยู่ — บอททักได้เฉพาะเบอร์ใน TEST_PHONES")
        if st.button("รีเฟรช"):
            st.rerun()
        st.caption(f"อัปเดตล่าสุด {datetime.now():%H:%M:%S}")

    conn = connection()
    if not db.schema_exists(conn):
        st.error("ยังไม่มีตารางของบอท — รัน python -m bot init-db ก่อน")
        st.stop()
    if "flash" in st.session_state:
        st.success(st.session_state.pop("flash"))

    shops = reports.load_shops(conn, include_test)
    waiting = sum(1 for s in shops if s["needs_human"])
    due = sum(1 for s in reports.recontacts(shops) if s["recontact_on"] <= date.today())
    tabs = st.tabs([f"💬 รอพนักงาน ({waiting})", f"📅 นัดทักใหม่ ({due})", "📊 ภาพรวม", "🔎 ร้านทั้งหมด"])
    with tabs[0]:
        waiting_tab(conn, shops)
    with tabs[1]:
        recontact_tab(conn, shops)
    with tabs[2]:
        overview_tab(conn, shops, allowed)
    with tabs[3]:
        all_shops_tab(conn, shops)


main()
