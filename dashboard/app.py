"""Staff dashboard (Streamlit). Start it with: python -m bot dashboard

Colours and the Mitr font come from .streamlit/config.toml; the CSS below only adds what the theme cannot.
"""

import hmac
import sys
from datetime import date, datetime, timedelta
from html import escape
from pathlib import Path

import psycopg
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # Streamlit runs this file on its own

from bot import db, reports  # noqa: E402
from bot.config import dashboard_password, database_url, load_settings, test_phones  # noqa: E402
from bot.quota import QuotaRule, today_plan  # noqa: E402
from bot.reports import SHOP_STATE_LABELS, STAFF_OUTCOMES, STATUS_LABELS  # noqa: E402
from bot.safety import test_mode  # noqa: E402

st.set_page_config(page_title="ติดตามร้านใหม่", page_icon="🍜", layout="wide")

# The red double rule under the header and on top of each card is the rim of a rooster bowl.
CSS = """
<style>
:root { --chili: #C8322B; --leaf: #2F7A3F; --leaf-pale: #DCEBD7; --ink: #1F1A17; --muted: #6B675F;
        --line: #E3E5DE; --wash: #F1F3EE; }
.rim { position: relative; border-bottom: 3px solid var(--chili); padding: 0 0 .9rem; margin: 0 0 1.4rem; }
.rim::after { content: ""; position: absolute; left: 0; right: 0; bottom: -8px; border-bottom: 1px solid var(--chili); }
.rim h1 { font-size: 1.9rem; font-weight: 600; margin: 0; padding: 0; line-height: 1.3; }
.rim p { color: var(--muted); font-size: 1.05rem; margin: .3rem 0 0; }
.rim strong { color: var(--chili); font-weight: 500; }
[class*="st-key-card-"] { box-shadow: inset 0 3px 0 var(--chili); background: #fff; }
.shop-head { display: flex; flex-wrap: wrap; align-items: baseline; gap: .35rem .7rem; }
.shop-head .code { font-weight: 500; color: var(--chili); border: 1px solid var(--chili); border-radius: .4rem;
                   padding: 0 .45rem; font-size: .9rem; font-variant-numeric: tabular-nums; }
.shop-head .name { font-size: 1.25rem; font-weight: 500; }
.shop-head .when { margin-left: auto; color: var(--muted); font-size: .9rem; }
.facts { color: var(--muted); font-size: .92rem; margin: .2rem 0; }
.badge { display: inline-block; border-radius: 1rem; padding: 0 .55rem; font-size: .85rem; font-weight: 400; }
.badge.wait { background: #F8E1DF; color: #9E2620; }
.badge.good { background: var(--leaf-pale); color: #22592E; }
.badge.warn { background: #FBEBD3; color: #8A4B0B; }
.badge.plain { background: var(--wash); color: var(--muted); }
.chat { background: var(--wash); border-radius: .8rem; padding: .9rem; display: flex; flex-direction: column; gap: .6rem; }
.msg { display: flex; flex-direction: column; max-width: 85%; }
.msg.in { align-self: flex-start; }
.msg.out { align-self: flex-end; align-items: flex-end; }
.bubble { padding: .5rem .8rem; border-radius: 1rem; white-space: pre-wrap; line-height: 1.5; }
.msg.in .bubble { background: #fff; border: 1px solid var(--line); border-top-left-radius: .25rem; }
.msg.out .bubble { background: var(--leaf-pale); border-top-right-radius: .25rem; }
.msg .meta { color: var(--muted); font-size: .78rem; margin: .15rem .3rem 0; }
.chat .empty { color: var(--muted); text-align: center; }
.hint { color: var(--muted); font-size: .85rem; margin: .5rem 0 0; }
.hint b { color: var(--ink); font-weight: 500; }
.funnel + .hint { margin-top: 1.1rem; }
.funnel { display: grid; grid-template-columns: 9.5rem 1fr 3rem 3rem; gap: .55rem .8rem; align-items: center; }
.funnel .label { font-size: .95rem; }
.funnel .bar { height: .7rem; background: var(--wash); border-radius: 1rem; overflow: hidden; }
.funnel .bar span { display: block; height: 100%; background: var(--leaf); border-radius: 1rem; }
.funnel .count { text-align: right; font-weight: 500; font-variant-numeric: tabular-nums; }
.funnel .rate { color: var(--muted); font-size: .82rem; font-variant-numeric: tabular-nums; }
@media (max-width: 640px) {
  .rim h1 { font-size: 1.45rem; }
  .funnel { grid-template-columns: 1fr 3rem 3rem; }
  .funnel .label { grid-column: 1 / -1; margin-bottom: -.3rem; }
  .shop-head .when { margin-left: 0; width: 100%; }
  .msg { max-width: 95%; }
}
</style>
"""

BADGE_KIND = {"REPLIED": "wait", "PITCHED": "wait", "INTERESTED": "good", "CUSTOMER": "good",
              "ERROR": "warn", "NOT_FOUND": "warn", "ADDED": "warn"}


def fmt(moment) -> str:
    if moment is None:
        return "-"
    if isinstance(moment, datetime):
        return f"{moment.astimezone():%d/%m/%y %H:%M}"
    return f"{moment:%d/%m/%y}"


def phone_text(phone) -> str:
    return f"{phone[:3]}-{phone[3:6]}-{phone[6:]}" if phone and len(phone) == 10 else (phone or "-")


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


def html(markup: str) -> None:
    st.markdown(markup, unsafe_allow_html=True)


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


# ---- one shop ----

def shop_header(shop, when: str, due: bool = False) -> None:
    status = shop["status"]
    facts = [phone_text(shop["phone"]),
             f'<span class="badge {BADGE_KIND.get(status, "plain")}">{escape(STATUS_LABELS.get(status, status))}</span>']
    if shop["shop_state"]:
        facts.append(f"ร้าน{escape(SHOP_STATE_LABELS.get(shop['shop_state'], shop['shop_state']))}")
    if shop["line_name"]:
        facts.append(f"ชื่อใน LINE: {escape(shop['line_name'])}")
    if shop["do_not_contact"]:
        facts.append('<span class="badge wait">ห้ามทัก</span>')
    flag = '<span class="badge wait">ถึงวันนัดแล้ว</span> ' if due else ""
    html(f'<div class="shop-head"><span class="code">{escape(shop["code"])}</span>'
         f'<span class="name">{escape(shop["shop_name"])}</span><span class="when">{flag}{escape(when)}</span></div>'
         f'<div class="facts">{" · ".join(facts)}</div>')


def conversation(conn, shop) -> None:
    """The chat as LINE shows it: the shop on the left, our side in green on the right."""
    bubbles = []
    for direction, sender, text, at in reports.messages_of(conn, shop["id"]):
        who = "ร้าน" if direction == "in" else ("บอท" if sender == "bot" else "พนักงาน")
        bubbles.append(f'<div class="msg {direction}"><div class="bubble">{escape(text or "(ไม่มีข้อความ)")}</div>'
                       f'<div class="meta">{who} · {fmt(at)}</div></div>')
    html(f'<div class="chat">{"".join(bubbles) or "<div class=empty>ยังไม่มีข้อความ</div>"}</div>'
         f'<p class="hint">ตอบร้านใน LINE PC โดยค้นแชทด้วยรหัส <b>{escape(shop["code"])}</b> — '
         "ข้อความที่พนักงานพิมพ์ใน LINE จะไม่ขึ้นที่นี่ ให้สรุปไว้ในโน้ต</p>")


def _default_outcome(shop) -> int:
    by_status = {"INTERESTED": "interested", "CUSTOMER": "customer", "DECLINED": "declined", "CLOSED": "closed"}
    key = by_status.get(shop["status"]) or ("not_open_yet" if shop["shop_state"] == "NOT_OPEN_YET" else "talking")
    return list(STAFF_OUTCOMES).index(key)


def staff_form(conn, shop, key: str) -> None:
    with st.form(f"staff-{key}-{shop['id']}", border=False):
        outcome = st.radio("ผลการคุย", list(STAFF_OUTCOMES), index=_default_outcome(shop),
                           format_func=lambda k: STAFF_OUTCOMES[k].label)
        recontact = st.date_input("นัดทักใหม่วันที่", value=shop["recontact_on"], format="DD/MM/YYYY",
                                  help="ต้องใส่เมื่อร้านยังไม่เปิด ผลอื่นใส่หรือไม่ใส่ก็ได้")
        notes = st.text_area("โน้ต", value=shop["notes"] or "", placeholder="เช่น ขอใบราคา, ใช้ลูกชิ้นเจ้าอื่นอยู่",
                             height=90)
        do_not_contact = st.checkbox("ห้ามทักร้านนี้อีก", value=shop["do_not_contact"])
        answered = st.checkbox("ตอบร้านแล้ว (เอาออกจากรายการรอพนักงาน)", value=True)
        if st.form_submit_button("บันทึกผล", type="primary", icon=":material/check:"):
            try:
                reports.save_staff_update(conn, shop["id"], outcome, notes, recontact, answered, do_not_contact)
            except ValueError as exc:
                st.error(str(exc))
                return
            st.session_state["flash"] = f"บันทึกผล {shop['code']} {shop['shop_name']} แล้ว"
            st.rerun()


def shop_card(conn, shop, key: str, when: str, folded: bool = False, due: bool = False) -> None:
    with st.container(border=True, key=f"card-{key}-{shop['id']}"):
        shop_header(shop, when, due)
        body = st.expander("ดูบทสนทนาและบันทึกผล") if folded else st.container()
        with body:
            left, right = st.columns([3, 2], gap="large")
            with left:
                conversation(conn, shop)
            with right:
                staff_form(conn, shop, key)


# ---- tabs ----

def waiting_tab(conn, shops) -> None:
    waiting = reports.waiting_for_staff(shops)
    if not waiting:
        st.success("ไม่มีร้านรอคุยต่อ — ร้านที่ตอบกลับมาจะขึ้นที่นี่ทันที", icon=":material/done_all:")
        return
    for shop in waiting:
        shop_card(conn, shop, "waiting", f"ตอบเมื่อ {fmt(shop['last_reply_at'])}")


def recontact_tab(conn, shops) -> None:
    planned = reports.recontacts(shops)
    if not planned:
        st.info("ยังไม่มีร้านที่นัดทักใหม่ — ตั้งวันนัดได้ตอนบันทึกผล", icon=":material/event:")
        return
    today = date.today()
    for shop in planned:
        shop_card(conn, shop, "recontact", f"นัด {fmt(shop['recontact_on'])}", folded=True,
                  due=shop["recontact_on"] <= today)


def funnel_html(steps) -> str:
    total = steps[0][1] or 1
    rows, previous = [], None
    for label, count in steps:
        rate = f"{count / previous:.0%}" if previous else ""
        rows.append(f'<div class="label">{escape(label)}</div>'
                    f'<div class="bar"><span style="width:{count / total:.1%}"></span></div>'
                    f'<div class="count">{count}</div><div class="rate">{rate}</div>')
        previous = count or None
    return (f'<div class="funnel">{"".join(rows)}</div>'
            '<p class="hint">ตัวเลขสีเทา = กี่เปอร์เซ็นต์ของขั้นก่อนหน้าที่ผ่านมาถึงขั้นนี้</p>')


def overview_tab(shops, plan, rule, allowed) -> None:
    by_status = {}
    for shop in shops:
        by_status[shop["status"]] = by_status.get(shop["status"], 0) + 1
    messaged = sum(1 for s in shops if s["messaged_at"])
    replied = sum(1 for s in shops if s["first_reply_at"])

    cols = st.columns(5)
    cols[0].metric("รอทักในคิว", by_status.get("PENDING", 0) + by_status.get("ADDED", 0), border=True)
    cols[1].metric("ทักแล้ว", messaged, border=True)
    cols[2].metric(f"ร้านตอบ ({replied / messaged:.0%})" if messaged else "ร้านตอบ", replied, border=True)
    cols[3].metric("สนใจ", by_status.get("INTERESTED", 0) + by_status.get("CUSTOMER", 0), border=True)
    cols[4].metric("เป็นลูกค้า", by_status.get("CUSTOMER", 0), border=True)

    if rule.fixed is None:
        quota_text = (f"บอททักวันละ {plan.quota} ร้าน ให้ร้านที่เหลือ {plan.total} ร้านหมดใน {plan.days_left} วัน "
                      f"(รอบละ {rule.finish_days} วัน)")
        if plan.capped:
            quota_text += f" — ติดเพดาน {rule.cap} ร้าน/วัน จะใช้ราว {plan.days_to_finish} วัน"
    else:
        quota_text = f"บอททักวันละ {plan.quota} ร้าน ร้านที่เหลือ {plan.total} ร้านใช้ราว {plan.days_to_finish} วัน"
    if allowed is not None:
        quota_text += " · โหมดทดสอบ: นับเฉพาะเบอร์ใน TEST_PHONES"
    st.caption(quota_text)

    left, right = st.columns([5, 4], gap="large")
    with left:
        st.subheader("Funnel ต่อรอบนำเข้า")
        batches = sorted({reports.batch_of(s) for s in shops}, reverse=True)
        batch = st.selectbox("รอบ (เดือนที่นำเข้า)", ["ทุกรอบ"] + batches)
        chosen = shops if batch == "ทุกรอบ" else [s for s in shops if reports.batch_of(s) == batch]
        html(funnel_html(reports.funnel(chosen)))
    with right:
        st.subheader("30 วันล่าสุด")
        activity = reports.daily_activity(shops, date.today() - timedelta(days=29))
        if activity:
            rows = [{"วันที่": day.isoformat(), **counts} for day, counts in activity.items()]  # ISO sorts by date
            st.bar_chart(rows, x="วันที่", stack=False, height=260)
        else:
            st.caption("ยังไม่มีการทักใน 30 วันล่าสุด")
        st.subheader("ร้านตามสถานะ")
        st.dataframe([{"สถานะ": STATUS_LABELS.get(k, k), "จำนวน": v}
                      for k, v in sorted(by_status.items(), key=lambda item: -item[1])], hide_index=True)


def all_shops_tab(conn, shops) -> None:
    search_col, status_col = st.columns([2, 3])
    query = search_col.text_input("ค้นหา", placeholder="รหัส ชื่อร้าน หรือเบอร์").strip().lower()
    statuses = status_col.multiselect("สถานะ", list(STATUS_LABELS), format_func=lambda k: STATUS_LABELS[k],
                                      placeholder="ทุกสถานะ")
    found = [s for s in shops
             if (not statuses or s["status"] in statuses)
             and (not query or query in f"{s['code']} {s['shop_name']} {s['phone'] or ''}".lower())]
    st.caption(f"{len(found)} ร้าน")
    st.dataframe([{
        "รหัส": s["code"], "ร้าน": s["shop_name"], "เบอร์": phone_text(s["phone"]),
        "สถานะ": STATUS_LABELS.get(s["status"], s["status"]), "ทักเมื่อ": fmt(s["messaged_at"]),
        "คำตอบล่าสุด": s["last_reply_text"] or "", "นัดทักใหม่": fmt(s["recontact_on"]) if s["recontact_on"] else "",
        "โน้ต": s["notes"] or "",
    } for s in found], hide_index=True)
    by_code = {s["code"]: s for s in found}
    picked = st.selectbox("เปิดร้านเพื่อดูบทสนทนาและบันทึกผล", [None] + list(by_code),
                          format_func=lambda code: "เลือกร้าน" if code is None else f"{code} {by_code[code]['shop_name']}")
    if picked is not None:
        shop_card(conn, by_code[picked], "all", f"ทักเมื่อ {fmt(by_code[picked]['messaged_at'])}")


def header(waiting: int, due: int, plan) -> None:
    parts = [f"มี <strong>{waiting} ร้าน</strong> รอคุยต่อ" if waiting else "ไม่มีร้านรอคุยต่อ"]
    if due:
        parts.append(f"ถึงวันนัดทักใหม่ <strong>{due} ร้าน</strong>")
    parts.append(f"วันนี้บอททัก {plan.quota} ร้าน")
    html(f'<div class="rim"><h1>ติดตามร้านก๋วยเตี๋ยวเปิดใหม่</h1><p>{" · ".join(parts)}</p></div>')


def main() -> None:
    html(CSS)
    require_password()
    settings = load_settings()
    allowed = test_phones() if test_mode(settings) else None

    with st.sidebar:
        st.subheader("🍜 ติดตามร้านใหม่")
        include_test = st.toggle("แสดงร้านทดสอบ", value=allowed is not None)
        if allowed is not None:
            st.caption("โหมดทดสอบเปิดอยู่ — บอททักได้เฉพาะเบอร์ใน TEST_PHONES")
        if st.button("โหลดข้อมูลใหม่", icon=":material/refresh:"):
            st.rerun()
        st.caption(f"ข้อมูลล่าสุด {datetime.now():%H:%M:%S}")

    conn = connection()
    if not db.schema_exists(conn):
        st.error("ยังไม่มีตารางของบอท — รัน python -m bot init-db ก่อน")
        st.stop()

    shops = reports.load_shops(conn, include_test)
    rule = QuotaRule.from_config(settings["outreach"])
    plan = today_plan(conn, rule, allowed, None, datetime.now().astimezone())
    waiting = sum(1 for s in shops if s["needs_human"])
    due = sum(1 for s in reports.recontacts(shops) if s["recontact_on"] <= date.today())
    header(waiting, due, plan)
    if "flash" in st.session_state:
        st.toast(st.session_state.pop("flash"), icon=":material/check_circle:")

    tabs = st.tabs([f":material/forum: รอพนักงาน ({waiting})", f":material/event: นัดทักใหม่ ({due})",
                    ":material/insights: ภาพรวม", ":material/storefront: ร้านทั้งหมด"])
    with tabs[0]:
        waiting_tab(conn, shops)
    with tabs[1]:
        recontact_tab(conn, shops)
    with tabs[2]:
        overview_tab(shops, plan, rule, allowed)
    with tabs[3]:
        all_shops_tab(conn, shops)


main()
