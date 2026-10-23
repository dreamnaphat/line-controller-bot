"""Staff dashboard (Streamlit). Start it with: python -m bot dashboard"""

import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # Streamlit runs this file on its own

from dashboard import bot_page, test_page, tracking  # noqa: E402
from dashboard.common import CSS, html, require_password  # noqa: E402

st.set_page_config(page_title="ติดตามร้านใหม่", page_icon="🍜", layout="wide")
html(CSS)
require_password()
if "flash" in st.session_state:  # set by a page just before st.rerun()
    st.toast(st.session_state.pop("flash"), icon=":material/check_circle:")
page = st.navigation([
    st.Page(bot_page.render, title="บอท", icon=":material/smart_toy:", default=True),
    st.Page(tracking.render, title="ติดตามร้าน", icon=":material/forum:", url_path="tracking"),
    st.Page(test_page.render, title="เครื่องมือทดสอบ", icon=":material/science:", url_path="testing"),
])
page.run()
