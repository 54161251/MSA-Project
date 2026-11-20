"""Streamlit entry point: sign in, then the member portal or the staff dashboard.

    python -m msa.cli ui      (the API must be running: python -m msa.cli api)
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:  # `streamlit run src/msa/ui/Home.py` from anywhere
    sys.path.insert(0, str(ROOT))

import streamlit as st  # noqa: E402

from msa.ui import api, common  # noqa: E402
from msa.ui.views import member, staff  # noqa: E402

st.set_page_config(page_title="Makerspace Safety Agent", page_icon=":material/construction:",
                   layout="wide")
common.setup_page()


def sign_in_page() -> None:
    st.markdown("<div class='msa-hero'><h1>Makerspace Safety Agent</h1>"
                "<p>Agents draft; staff decide.</p></div>", unsafe_allow_html=True)
    tab_in, tab_up = st.tabs(["Sign in", "Create an account"])
    with tab_in, st.form("signin"):
        email = st.text_input("Email")
        pw = st.text_input("Password", type="password")
        if st.form_submit_button("Sign in", type="primary"):
            res = api.safe(api.post, "/auth/login", {"email": email, "password": pw})
            if res:
                st.session_state["token"] = res["token"]
                st.session_state["user"] = res["user"]
                st.rerun()
    with tab_up, st.form("signup"):
        st.caption("Members must be 15 or older. Younger makers are added by a parent or "
                   "guardian from their own account.")
        name = st.text_input("Name")
        email = st.text_input("Email", key="su_email")
        year = st.number_input("Birth year", min_value=1900, max_value=2100, value=2000, step=1)
        pw = st.text_input("Password (8+ characters)", type="password", key="su_pw")
        with st.expander("User agreement and liability waiver"):
            st.write("PLACEHOLDER TEXT. A real makerspace replaces this with its own agreement, "
                     "reviewed by its lawyer. Members aged 15 to 17 need a guardian to sign the "
                     "waiver before using any machine.")
        agree = st.checkbox("I have read and accept the user agreement")
        if st.form_submit_button("Create account", type="primary"):
            res = api.safe(api.post, "/auth/signup", {"name": name, "email": email, "password": pw,
                                                     "birth_year": int(year), "agreed_to_terms": agree})
            if res:
                st.success("Account created. You can sign in now.")


user = st.session_state.get("user")
if not user:
    st.navigation([st.Page(sign_in_page, title="Sign in", icon=":material/login:")]).run()
else:
    if "site" not in st.session_state:
        st.session_state["site"] = api.safe(api.get, "/settings") or {}
    common.emergency_sidebar()
    audience = "staff" if user["role"] == "staff" else "member"
    common.sidebar_nav_css(user["role"])
    with st.sidebar:
        st.markdown(f"Signed in as **{user['name']}**<br>{common.chip(audience)}", unsafe_allow_html=True)
        st.caption("Colour key")
        common.colour_key(user["role"])
        if st.button("Sign out", icon=":material/logout:"):
            for k in ("token", "user", "site"):
                st.session_state.pop(k, None)
            st.rerun()
    if user["role"] == "staff":
        pages = {"Staff only": staff.pages(), "Member pages": member.pages()}
    else:
        pages = {"Makerspace": member.pages()}
    st.navigation(pages, expanded=True).run()
