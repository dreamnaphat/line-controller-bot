"""Shared pieces of the dashboard pages: styles, formatting, the database connection and the password gate.

Colours and the Mitr font come from .streamlit/config.toml; the CSS below only adds what the theme cannot.
"""

import hmac
from datetime import datetime

import psycopg
import streamlit as st

from bot import db
from bot.config import dashboard_password, database_url

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
[class*="st-key-panel-"] h3 { font-size: 1.35rem; }
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


def rim(title: str, subtitle: str) -> None:
    """Page header; `subtitle` is HTML, so escape anything that came from the database."""
    html(f'<div class="rim"><h1>{title}</h1><p>{subtitle}</p></div>')
