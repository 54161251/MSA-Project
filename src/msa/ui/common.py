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
# Coloured by how serious the finding is (the icon shape differs too, so colour is never alone).
SEVERITY_ICON = {"critical": ":red[:material/emergency:]", "high": ":orange[:material/warning:]",
                 "medium": ":orange[:material/info:]", "condition": ":blue[:material/checklist:]",
                 "info": ":gray[:material/info:]", "ok": ":green[:material/check:]"}
UNITS = {"m": 1.0, "ft": 0.3048, "in": 0.0254, "cm": 0.01}

# ------------------------------------------------------------------ colours
# Team palette (from the presentation deck). Each colour has one job, explained in the sidebar key:
#   maroon  = staff only        coral  = member pages        yellow = waits for a staff decision
#   black and yellow stripes = emergency.   Machine and training status keep green / orange / red
#   (with an icon and words) because those colours carry safety meaning.
MAROON, MAROON_DARK, CORAL, CORAL_TEXT = "#730E0B", "#4D0705", "#E8695C", "#B8443A"
PINK, TINT, YELLOW, INK = "#F3B2A6", "#FBEFEC", "#F8D66D", "#2A1210"

AUDIENCE = {
    "staff": {"color": MAROON, "tint": "#F6E4E2", "label": "Staff only", "icon": "lock"},
    "member": {"color": CORAL_TEXT, "tint": "#FDEEEA", "label": "Member page", "icon": "groups"},
}

CSS = f"""
<style>
html, body, [class*="css"], .stMarkdown, .stText, p, li, label {{ font-size: 1.08rem; }}
.msa-icon {{ font-family: "Material Symbols Rounded"; font-weight: normal; font-style: normal;
            line-height: 1; letter-spacing: normal; text-transform: none; white-space: nowrap;
            direction: ltr; -webkit-font-smoothing: antialiased; font-feature-settings: "liga"; }}
.msa-emergency {{ border: 5px solid transparent; border-radius: 10px; padding: 0.6rem 0.9rem;
                 font-weight: 600; color: {INK};
                 background: linear-gradient(#FFFBEA, #FFFBEA) padding-box,
                             repeating-linear-gradient(45deg, #111 0 10px, {YELLOW} 10px 20px) border-box; }}
.msa-emergency .msa-icon {{ color: #B00020; font-size: 1.3em; vertical-align: -0.25em; margin-right: 0.3em; }}
[data-testid="stSidebar"] .msa-emergency {{ margin-bottom: 0.9rem; }}
.msa-cite {{ font-size: 0.9rem; opacity: 0.85; }}
.msa-header {{ display: flex; align-items: center; gap: 0.9rem; padding: 0.9rem 1.1rem; margin: 0 0 1rem;
              border-radius: 14px; border-left: 8px solid var(--msa-c); background: var(--msa-t); }}
.msa-header .msa-badge {{ display: grid; place-items: center; width: 3rem; height: 3rem; border-radius: 12px;
                         background: var(--msa-c); color: #fff; font-size: 1.8rem; flex: none; }}
.msa-header h1 {{ margin: 0; padding: 0; font-size: 2.1rem; color: {INK}; line-height: 1.15; }}
.msa-header .msa-sub {{ margin: 0.15rem 0 0; color: #5B3B37; font-size: 1rem; }}
.msa-chip {{ display: inline-flex; align-items: center; gap: 0.3em; padding: 0.15em 0.65em;
            border-radius: 999px; font-size: 0.85rem; font-weight: 600; color: #fff;
            background: var(--msa-c); white-space: nowrap; }}
.msa-chip .msa-icon {{ font-size: 1.05em; }}
.msa-header .msa-chip {{ margin-left: auto; }}
.msa-key {{ font-size: 0.9rem; line-height: 1.9; }}
.msa-key span.sw {{ display: inline-block; width: 0.9rem; height: 0.9rem; border-radius: 4px;
                   vertical-align: -0.12rem; margin-right: 0.45rem; }}
[data-testid="stMetric"] {{ background: {TINT}; border-radius: 12px; padding: 0.8rem 1rem;
                           border-top: 5px solid {MAROON}; }}
[data-testid="stMetricValue"] {{ color: {MAROON}; }}
.st-key-metric_approvals [data-testid="stMetric"] {{ border-top-color: {YELLOW}; background: #FFF8E1; }}
.st-key-metric_approvals [data-testid="stMetricValue"] {{ color: #6B4E00; }}
[class*="st-key-decide_"] {{ border-left: 8px solid {YELLOW} !important; background: #FFFCF0; }}
[class*="st-key-esc_"] {{ border-left: 8px solid {MAROON} !important; background: #FDF6F5; }}
[class*="st-key-due_"], [class*="st-key-order_"] {{ border-left: 8px solid {MAROON} !important; }}
[class*="st-key-rep_incident_"] {{ border-left: 8px solid #B00020 !important; background: #FFF5F5; }}
[class*="st-key-rep_near_miss_"] {{ border-left: 8px solid #E65100 !important; background: #FFF8F0; }}
[class*="st-key-rep_feedback_"] {{ border-left: 8px solid {CORAL} !important; }}
[class*="st-key-step_"] {{ border-left: 8px solid {CORAL} !important; }}
.msa-hero {{ background: {MAROON}; color: #fff; border-radius: 16px; padding: 1.6rem 1.8rem;
            margin-bottom: 1.2rem; border-bottom: 8px solid {CORAL}; }}
.msa-hero h1 {{ color: #fff; margin: 0; padding: 0; font-size: 2.4rem; }}
.msa-hero p {{ color: {YELLOW}; margin: 0.3rem 0 0; font-weight: 600; font-size: 1.15rem; }}
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
            f"<div class='msa-emergency' role='alert'><span class='msa-icon'>emergency</span>Emergency? Call "
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


# ------------------------------------------------------------ page chrome


def _audience_css(audience: str) -> str:
    """Primary buttons and active tab follow the page's audience colour."""
    c = AUDIENCE[audience]["color"]
    hover = MAROON_DARK if audience == "staff" else "#9C372E"
    return (f"<style>button[kind^='primary'] {{ background: {c} !important; border-color: {c} !important; "
            f"color: #fff !important; }} button[kind^='primary']:hover {{ background: {hover} !important; "
            f"border-color: {hover} !important; }}</style>")


def chip(audience: str) -> str:
    a = AUDIENCE[audience]
    return (f"<span class='msa-chip' style='--msa-c:{a['color']}'><span class='msa-icon'>{a['icon']}</span>"
            f"{a['label']}</span>")


def page_header(title: str, icon: str, audience: str, subtitle: str | None = None) -> None:
    """Coloured page title: the colour and the chip say who the page is for."""
    a = AUDIENCE[audience]
    sub = f"<p class='msa-sub'>{subtitle}</p>" if subtitle else ""
    st.markdown(
        f"<div class='msa-header' style='--msa-c:{a['color']};--msa-t:{a['tint']}'>"
        f"<span class='msa-badge'><span class='msa-icon'>{icon}</span></span>"
        f"<div><h1>{title}</h1>{sub}</div>{chip(audience)}</div>" + _audience_css(audience),
        unsafe_allow_html=True)


def sidebar_nav_css(role: str) -> None:
    """Colour each sidebar section (titles, icons, current page) by who it is for."""
    groups = ["staff", "member"] if role == "staff" else ["member"]
    css = []
    for n, aud in enumerate(groups, start=1):
        c, t = AUDIENCE[aud]["color"], AUDIENCE[aud]["tint"]
        css.append(f'[data-testid="stSidebarNavItems"] > div:nth-of-type({n}) header p '
                   f'{{ color: {c}; font-weight: 700; letter-spacing: 0.02em; }}')
        css.append(f'[data-testid="stSidebarNavItems"] > div:nth-of-type({n}) [data-testid="stIconMaterial"] '
                   f'{{ color: {c} !important; }}')
        css.append(f'[data-testid="stSidebarNavItems"] > div:nth-of-type({n}) a[aria-current="page"] '
                   f'{{ background: {t}; box-shadow: inset 4px 0 0 {c}; }}')
    st.markdown("<style>" + "\n".join(css) + "</style>", unsafe_allow_html=True)


def colour_key(role: str) -> None:
    rows = []
    if role == "staff":
        rows.append((MAROON, "Staff only"))
    rows += [(CORAL, "Member pages"), (YELLOW, "Waits for a staff decision")]
    html = "".join(f"<div><span class='sw' style='background:{c}'></span>{t}</div>" for c, t in rows)
    st.markdown(f"<div class='msa-key'>{html}</div>", unsafe_allow_html=True)
