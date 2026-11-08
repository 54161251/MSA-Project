"""Shared UI pieces: styling, status badges, citations, the emergency banner, units."""

from __future__ import annotations

from typing import Any

import streamlit as st

from msa.ui import api

# Status is never shown by colour alone: every badge has an icon and words.
STATUS = {
    "available": (":material/check_circle:", "Available", "green"),
    "issue_reported": (":material/report:", "Issue reported", "orange"),
    "maintenance_due": (":material/build:", "Maintenance due", "orange"),
    "out_of_service": (":material/block:", "Out of service", "red"),
    "retired": (":material/archive:", "Retired", "gray"),
}
TRAINING = {
    "certified": (":material/verified:", "Certified", "green"),
    "pending_check": (":material/hourglass_top:", "Waiting for in-person check", "orange"),
    "quiz_passed": (":material/task_alt:", "Quiz passed", "blue"),
    "not_started": (":material/radio_button_unchecked:", "Not trained", "gray"),
    "expired": (":material/event_busy:", "Expired", "red"),
    "revoked": (":material/cancel:", "Revoked", "red"),
}
SEVERITY_ICON = {"critical": ":material/emergency:", "high": ":material/warning:",
                 "medium": ":material/info:", "condition": ":material/checklist:",
                 "info": ":material/info:", "ok": ":material/check:"}
UNITS = {"m": 1.0, "ft": 0.3048, "in": 0.0254, "cm": 0.01}

CSS = """
<style>
html, body, [class*="css"], .stMarkdown, .stText, p, li, label { font-size: 1.08rem; }
.msa-emergency { border: 3px solid #b00020; border-radius: 8px; padding: 0.6rem 0.9rem;
                 font-weight: 600; }
.msa-cite { font-size: 0.9rem; opacity: 0.85; }
</style>
"""


def setup_page() -> None:
    st.markdown(CSS, unsafe_allow_html=True)


def badge(kind: str, value: str) -> str:
    table = STATUS if kind == "status" else TRAINING
    icon, label, color = table.get(value, (":material/help:", value.replace("_", " "), "gray"))
    return f":{color}-badge[{icon} {label}]"


def emergency_sidebar() -> None:
    s = st.session_state.get("site") or {}
    with st.sidebar:
        st.markdown(
            f"<div class='msa-emergency' role='alert'>Emergency? Call "
            f"{s.get('emergency_number', '911')} first, then tell staff.</div>",
            unsafe_allow_html=True)


def emergency_block(out: dict[str, Any]) -> None:
    st.error(f"**{out.get('message') or out.get('answer')}**", icon=":material/emergency:")
    if out.get("note"):
        st.write(out["note"])
    if out.get("safety_gear"):
        st.write("Safety equipment on the floor plan: " + ", ".join(out["safety_gear"]))


def citations(cites: list[dict], label: str = "Sources") -> None:
    if not cites:
        return
    with st.expander(f"{label} ({len(cites)})", icon=":material/menu_book:"):
        for c in cites:
            q = f' "{c["quote"]}"' if c.get("quote") else ""
            st.markdown(f"<div class='msa-cite'>{c.get('locator', '')}{q}</div>", unsafe_allow_html=True)


def to_units(m: float | None, units: str) -> str:
    if m is None:
        return "?"
    v = m / UNITS[units]
    return f"{v:.2f} {units}"


def equipment_options(include_none: bool = False) -> dict[str, str]:
    eqs = api.safe(api.get, "/equipment") or []
    opts = {e["id"]: e["name"] for e in eqs}
    return ({"": "(not about a machine)"} | opts) if include_none else opts


def statements_view(statements: list[dict], flags: list[dict] | None = None) -> None:
    """Render an SOP/training doc as sections, with each statement's sources on hover."""
    flagged = {f.get("index"): f for f in (flags or []) if f.get("index") is not None}
    current = None
    for i, s in enumerate(statements):
        if s["section"] != current:
            current = s["section"]
            st.markdown(f"#### {current}")
        refs = "; ".join(c.get("locator", "") for c in s.get("citations", []))
        line = f"- {s['text']}"
        if i in flagged:
            line += f"  \n  :red-badge[:material/flag: {flagged[i]['code']}] {flagged[i].get('why', '')}"
        st.markdown(line, help=f"Source: {refs}" if refs else "No source")
