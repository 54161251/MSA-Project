"""Staff dashboard pages. Every human checkpoint in the system can be completed here."""

from __future__ import annotations

import streamlit as st

from msa.ui import api, common
from msa.ui.floorplan import floorplan

KIND_LABEL = {
    "publish_doc": "Publish a document", "quiz_bank": "Approve a quiz",
    "competency_check": "In-person competency check", "maintenance_plan": "Maintenance plan",
    "order_draft": "Order draft", "layout_change": "Layout suggestion",
    "project_check": "Project needs review", "status_override": "Status override",
}


def pages() -> list:
    return [
        st.Page(briefing_page, title="Today", icon=":material/dashboard:", url_path="today", default=True),
        st.Page(approvals_page, title="Approvals", icon=":material/approval:", url_path="approvals"),
        st.Page(alerts_page, title="Alerts", icon=":material/notifications:", url_path="alerts"),
        st.Page(equipment_admin_page, title="Manage equipment", icon=":material/settings:",
                url_path="manage-equipment"),
        st.Page(people_page, title="People & permissions", icon=":material/group:", url_path="people"),
        st.Page(maintenance_page, title="Maintenance", icon=":material/build:", url_path="maintenance"),
        st.Page(reports_page, title="Reports", icon=":material/report:", url_path="reports"),
        st.Page(inventory_admin_page, title="Inventory & orders", icon=":material/inventory:",
                url_path="inventory-admin"),
        st.Page(floorplan_editor_page, title="Floor plan editor", icon=":material/edit_location:",
                url_path="floorplan-editor"),
        st.Page(settings_page, title="Settings & logs", icon=":material/tune:", url_path="settings"),
    ]


# -------------------------------------------------------------------- today


def briefing_page() -> None:
    common.page_header("Today", "dashboard", "staff")
    b = api.safe(api.get, "/briefing")
    if not b:
        return
    f = b["output"]["facts"]
    st.info(b["output"]["summary"], icon=":material/summarize:")
    c = st.columns(4)
    c[0].container(key="metric_approvals").metric("Approvals waiting",
                                                  sum(a["count"] for a in f["pending_approvals"]))
    c[1].metric("Open alerts", sum(e["count"] for e in f["escalations"]))
    c[2].metric("Machines not available", len(f["machines_not_available"]))
    c[3].metric("Maintenance due", len(f.get("maintenance_due", [])))
    if f["escalations"]:
        st.subheader("Open alerts")
        for e in f["escalations"]:
            st.markdown(f"- {e['what'][0].upper()}{e['what'][1:]}")
        st.caption("Details and the Resolve buttons are on the Alerts page.")
    if f["pending_approvals"]:
        st.subheader("Waiting for your approval")
        for a in f["pending_approvals"]:
            st.markdown(f"- {a['what'][0].upper()}{a['what'][1:]}")
    if f["machines_not_available"]:
        st.subheader("Machines")
        for m in f["machines_not_available"]:
            st.markdown(f"{common.badge('status', m['status'])} **{m['name']}**: {m['reason']}")
    if f.get("low_stock"):
        st.subheader("Low stock")
        for i in f["low_stock"]:
            st.markdown(f"- {i['item']}: {i['on_hand']:g} {i['unit']}")
    if st.button("Run the daily check now", icon=":material/refresh:"):
        api.safe(api.post, "/admin/run-daily")
        st.rerun()


# ---------------------------------------------------------------- approvals


def approvals_page() -> None:
    common.page_header("Approvals", "approval", "staff",
                       "Agents draft; staff decide. Nothing here affects members until you approve it.")
    items = api.safe(api.get, "/approvals") or []
    if not items:
        st.success("Nothing waiting.", icon=":material/done_all:")
        return
    kinds = sorted({a["kind"] for a in items})
    pick = st.segmented_control("Show", ["all", *kinds], default="all",
                                format_func=lambda k: "All" if k == "all" else KIND_LABEL.get(k, k))
    for a in items:
        if pick not in (None, "all") and a["kind"] != pick:
            continue
        with st.container(border=True, key=f"decide_{a['id']}"):
            st.markdown(f"**{KIND_LABEL.get(a['kind'], a['kind'])}**: {a['summary']}")
            preview(a)
            reason = st.text_input("Note (required to reject)", key=f"r_{a['id']}")
            c1, c2 = st.columns(2)
            if c1.button("Approve", key=f"ok_{a['id']}", type="primary", icon=":material/check:"):
                if api.safe(api.post, f"/approvals/{a['id']}/decide", {"approve": True, "reason": reason}):
                    st.rerun()
            if c2.button("Reject", key=f"no_{a['id']}", icon=":material/close:"):
                if api.safe(api.post, f"/approvals/{a['id']}/decide", {"approve": False, "reason": reason}):
                    st.rerun()


def preview(a: dict) -> None:
    kind, p = a["kind"], a.get("payload", {})
    with st.expander("Details"):
        if kind == "publish_doc":
            d = api.safe(api.get, f"/documents/{a['target_id']}") or {}
            rv = d.get("review") or {}
            if rv.get("summary"):
                st.caption(f"Review agent: {rv['summary']}")
            for r in rv.get("removed_in_revision", []):
                st.markdown(f":orange-badge[:material/delete: removed] {r['text']} ({r['code']}: {r['why']})")
            common.statements_view(d.get("statements", []), rv.get("flags"))
        elif kind == "quiz_bank":
            eq = p.get("equipment_id")
            banks = api.safe(api.get, f"/quiz/{eq}/banks") or []
            bank = next((b for b in banks if b["id"] == a["target_id"]), None)
            for q in (bank or {}).get("questions", []):
                st.markdown(f"**{q['prompt']}**")
                for i, o in enumerate(q["options"]):
                    st.markdown(("- :green[:material/check:] " if i == q["answer_index"] else "- ") + o)
        elif kind == "maintenance_plan":
            m = api.safe(api.get, f"/maintenance/{a['target_id']}") or {}
            for pl in m.get("plans", []):
                if pl["id"] in p.get("plan_ids", []):
                    every = f"every {pl['interval_hours']:g} h of use" if pl.get("interval_hours") \
                        else f"every {pl['interval_days']} days"
                    st.markdown(f"- {pl['task']} ({every})")
        elif kind == "order_draft":
            orders = api.safe(api.get, "/inventory/orders") or []
            o = next((x for x in orders if x["id"] == a["target_id"]), None)
            for line in (o or {}).get("items", []):
                st.markdown(f"- {line['qty']:g} {line['unit']} {line['name']} (on hand {line['on_hand']:g})")
            st.caption("Approving does not place the order; order from the supplier yourself.")
        elif kind == "competency_check":
            st.write(f"Score {p.get('score', 0):.0%} on the quiz. Watch the member use the machine "
                     "safely before approving.")
        elif kind == "project_check":
            pc = api.safe(api.get, f"/project-checks/{a['target_id']}") or {}
            st.write(pc.get("description", ""))
            for f in pc.get("findings", []):
                st.markdown(f"{common.SEVERITY_ICON.get(f['severity'], '')} {f['text']}")
        elif kind == "layout_change":
            for pr in p.get("proposals", []):
                st.markdown(f"- {pr['label']} at x={pr['x']:.2f} m, y={pr['y']:.2f} m, "
                            f"rotated {pr['rotation']:g}°")
        else:
            st.json(p)


# ------------------------------------------------------------------- alerts


URGENCY = {
    "urgent": ("Urgent", ":red[:material/emergency:]", "Injuries, fire, dangerous requests and "
               "system failures. Deal with these first."),
    "action": ("Needs a decision", ":orange[:material/front_hand:]", "A member was stopped and "
               "may need help, or a draft needs fixing."),
    "info": ("For information", ":gray[:material/info:]", "Nobody is blocked; check when you can."),
}


def _ago(ts: str) -> str:
    from datetime import datetime, timezone
    try:
        t = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return ts[:16]
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    mins = int((datetime.now(timezone.utc) - t).total_seconds() // 60)
    if mins < 1:
        return "just now"
    if mins < 60:
        return f"{mins} min ago"
    if mins < 60 * 24:
        return f"{mins // 60} h ago"
    return f"{mins // (60 * 24)} days ago"


def alerts_page() -> None:
    common.page_header("Alerts", "notifications", "staff",
                       "Things the agents stopped and handed to a person. Resolve each one with a "
                       "short note of what you did.")
    esc = api.safe(api.get, "/escalations") or []
    notes = api.safe(api.get, "/notifications") or []
    t1, t2 = st.tabs([f"Alerts ({len(esc)})", f"Notifications ({len(notes)})"])
    with t1:
        if not esc:
            st.success("No open alerts.", icon=":material/done_all:")
        groups = {k: [e for e in esc if e.get("urgency", "action") == k] for k in URGENCY}
        c = st.columns(3)
        for col, (k, (label, _icon, _)) in zip(c, URGENCY.items(), strict=True):
            col.metric(label, len(groups[k]))
        for k, (label, icon, help_text) in URGENCY.items():
            if not groups[k]:
                continue
            st.subheader(f"{icon} {label}")
            st.caption(help_text)
            for e in groups[k]:
                with st.container(border=True, key=f"esc_{e['id']}"):
                    head, when = st.columns([5, 1])
                    head.markdown(f"**{e.get('what') or e['code']}**")
                    when.caption(_ago(e["created_at"]))
                    if e.get("about"):
                        st.markdown(e["about"])
                    lines = [ln for ln in (e.get("details_readable") or e["details"]).splitlines()
                             if ln.strip()]
                    if lines:
                        shown = "\n".join(f"- {ln}" for ln in lines[:5])
                        more = f"\n- …and {len(lines) - 5} more" if len(lines) > 5 else ""
                        st.markdown(shown + more)
                    a, b = st.columns([4, 1], vertical_alignment="bottom")
                    note = a.text_input("What did you do?", key=f"en_{e['id']}",
                                        placeholder="e.g. Spoke to the member, showed them the guard")
                    if b.button("Resolve", key=f"er_{e['id']}", type="primary"):
                        if api.safe(api.post, f"/escalations/{e['id']}/resolve", {"note": note}):
                            st.rerun()
                    st.caption(f"Code {e['code']}")
    with t2:
        if not notes:
            st.success("No open notifications.", icon=":material/done_all:")
        for n in notes:
            cols = st.columns([6, 1])
            count = f" (x{n['count']})" if n["count"] > 1 else ""
            cols[0].markdown(f"{common.SEVERITY_ICON.get(n['severity'], '')} {n['message']}{count}")
            if cols[1].button("Done", key=f"nd_{n['id']}"):
                api.safe(api.post, f"/notifications/{n['id']}/resolve")
                st.rerun()


# --------------------------------------------------------- equipment admin


CATEGORIES = ["laser_cutter", "fdm_printer", "resin_printer", "cnc_router", "table_saw", "bandsaw",
              "drill_press", "soldering", "sewing", "vinyl_cutter", "other"]


def equipment_admin_page() -> None:
    common.page_header("Manage equipment", "settings", "staff")
    t_add, t_edit = st.tabs(["Add equipment", "Edit equipment"])
    with t_add, st.form("add_eq"):
        st.caption("The Intake agent finds or reads the manual, then drafts the SOP, training guide "
                   "and materials list. The Review agent checks them before they reach Approvals.")
        c1, c2 = st.columns(2)
        eid = c1.text_input("Short id (e.g. laser-02)")
        name = c2.text_input("Name")
        brand = c1.text_input("Brand")
        model = c2.text_input("Model number")
        cat = c1.selectbox("Category", CATEGORIES)
        mode = c2.selectbox("Approval after quiz", ["competency_check", "auto"],
                            format_func={"auto": "Automatic on passing",
                                         "competency_check": "In-person check by staff"}.get)
        manual = st.file_uploader("Manual (PDF, text or markdown; leave empty to let the agent look "
                                  "it up)", type=["pdf", "txt", "md"])
        notes = st.text_area("Staff notes / house rules (optional)",
                             placeholder="- Never: MDF. Our extraction can't handle it.")
        go = st.form_submit_button("Run intake", type="primary")
    if go:
        if manual is not None:
            created = api.safe(api.post, "/intake", {"id": eid, "name": name, "brand": brand,
                                                      "model_number": model, "category": cat,
                                                      "approval_mode": mode, "run_now": False})
            if created:
                new_id = created["final"]["output"]["equipment_id"]
                res = api.safe(api.call, "POST", f"/intake/{new_id}/manual",
                               files={"file": (manual.name, manual.getvalue())}, data={"notes": notes})
                show_intake(res)
        else:
            res = api.safe(api.post, "/intake", {
                "id": eid, "name": name, "brand": brand, "model_number": model, "category": cat,
                "approval_mode": mode,
                "staff_notes": [{"title": "House rules", "text": notes}] if notes.strip() else []})
            show_intake(res)
    with t_edit:
        opts = common.equipment_options()
        if not opts:
            return
        eid = st.selectbox("Machine", list(opts), format_func=opts.get, key="edit_pick")
        data = api.safe(api.get, f"/equipment/{eid}")
        if not data:
            return
        eq = data["equipment"]
        st.markdown(common.badge("status", eq["status"]))
        units = st.segmented_control("Units", list(common.UNITS), default="ft", key="eq_units") or "ft"
        f = common.UNITS[units]
        with st.form("edit_eq"):
            c1, c2 = st.columns(2)
            mode = c1.selectbox("Approval after quiz", ["competency_check", "auto"],
                                index=0 if eq["approval_mode"] == "competency_check" else 1)
            months = c2.number_input("Training valid (months)", 1, 120, eq["training_valid_months"])
            prereq = st.multiselect("Prerequisite training", [k for k in opts if k != eid],
                                    default=eq["prerequisites"], format_func=opts.get)
            fw = c1.number_input(f"Footprint width ({units})", 0.0, 100.0, round(eq["footprint_w"] / f, 2))
            fd = c2.number_input(f"Footprint depth ({units})", 0.0, 100.0, round(eq["footprint_d"] / f, 2))
            cl = eq.get("clearance") or {}
            cc = st.columns(4)
            clear = {side: cc[i].number_input(f"Clearance {side} ({units})", 0.0, 50.0,
                                              round(cl.get(side, 0.0) / f, 2))
                     for i, side in enumerate(["front", "back", "left", "right"])}
            if st.form_submit_button("Save", type="primary"):
                ok = api.safe(api.patch, f"/equipment/{eid}", {
                    "approval_mode": mode, "training_valid_months": int(months), "prerequisites": prereq,
                    "footprint_w": fw * f, "footprint_d": fd * f,
                    "clearance": {k: v * f for k, v in clear.items()}})
                if ok:
                    st.success("Saved")
        with st.form("status"):
            new = st.selectbox("Set status", list(common.STATUS), format_func=lambda s: common.STATUS[s][1])
            why = st.text_input("Reason (required)")
            if st.form_submit_button("Override status"):
                if api.safe(api.post, f"/equipment/{eid}/status", {"status": new, "reason": why}):
                    st.rerun()
        c1, c2 = st.columns(2)
        with c1, st.form("regen_quiz"):
            g = st.text_input("Quiz guidelines (optional)", placeholder="Focus on material rules")
            if st.form_submit_button("Draft a new quiz"):
                r = api.safe(api.post, f"/quiz/{eid}/generate", {"guidelines": g})
                if r:
                    st.success("Quiz drafted; see Approvals.")
        with c2:
            if st.button("Re-run intake from the manual"):
                show_intake(api.safe(api.post, "/intake", {"equipment_id": eid}))


def show_intake(res: dict | None) -> None:
    if not res:
        return
    st.subheader("What the agents did")
    for step in res.get("steps", []):
        st.markdown(f"**{step['agent']}**: {step['status']}"
                    + (f" (escalations: {', '.join(step['escalations'])})" if step["escalations"] else ""))
        for t in step.get("trace", []):
            st.caption(t)
    st.info("Drafts that passed review are waiting in Approvals. Problems are listed in Alerts.")


# ------------------------------------------------------------------- people


def people_page() -> None:
    common.page_header("People & permissions", "group", "staff")
    st.caption("Staff have full admin rights in this version; finer roles (steward, manager) "
               "could be added later.")
    users = api.safe(api.get, "/users") or []
    names = {u["id"]: f"{u['name']} ({u['email']})" for u in users}
    st.dataframe([{"Name": u["name"], "Email": u["email"], "Role": u["role"], "Born": u["birth_year"],
                   "Waiver": u["waiver_status"], "Can sign in": u["can_sign_in"]} for u in users],
                 hide_index=True, use_container_width=True)
    if not users:
        return
    uid = st.selectbox("Member", list(names), format_func=names.get)
    rec = api.safe(api.get, f"/users/{uid}") or {}
    u = rec.get("user", {})
    if u.get("waiver_status") == "guardian_required":
        st.warning("Guardian waiver not signed yet: no machine access.", icon=":material/gavel:")
        if st.button("Mark guardian waiver signed"):
            api.safe(api.post, f"/users/{uid}/waiver", {"waiver_status": "guardian_signed"})
            st.rerun()
    for t in rec.get("training", []):
        cols = st.columns([3, 2, 3])
        cols[0].write(t["equipment_name"])
        cols[1].markdown(common.badge("training", t["state"]))
        with cols[2].popover("Change"):
            new = st.selectbox("State", ["certified", "revoked", "not_started", "quiz_passed"],
                               key=f"ts_{t['equipment_id']}")
            why = st.text_input("Reason", key=f"tr_{t['equipment_id']}")
            if st.button("Apply", key=f"ta_{t['equipment_id']}"):
                if api.safe(api.post, f"/users/{uid}/training/{t['equipment_id']}",
                            {"state": new, "reason": why}):
                    st.rerun()


# -------------------------------------------------------------- maintenance


def maintenance_page() -> None:
    common.page_header("Maintenance", "build", "staff")
    due = api.safe(api.get, "/maintenance") or []
    st.subheader(f"Due now ({len(due)})")
    for w in due:
        with st.container(border=True, key=f"due_{w['plan_id']}"):
            st.markdown(f"**{w['equipment_name']}**: {w['task']} ({w['why']})")
            st.caption("Manual: " + " ".join(w["steps"]))
            note = st.text_input("Notes", key=f"mn_{w['plan_id']}")
            if st.button("Log as done", key=f"md_{w['plan_id']}", type="primary"):
                if api.safe(api.post, f"/maintenance/{w['equipment_id']}/log",
                            {"kind": "maintenance", "notes": note, "plan_id": w["plan_id"]}):
                    st.rerun()
    st.subheader("Log and history")
    opts = common.equipment_options()
    eid = st.selectbox("Machine", list(opts), format_func=opts.get, key="m_eq")
    data = api.safe(api.get, f"/maintenance/{eid}") or {}
    plans = {p["id"]: p["task"] for p in data.get("plans", []) if p["status"] == "approved"}
    with st.form("log"):
        kind = st.selectbox("Entry", ["maintenance", "inspection", "correction"])
        plan = st.selectbox("Task (optional)", ["", *plans], format_func=lambda k: plans.get(k, "(none)"))
        corr = st.selectbox("Corrects entry", ["", *[e["id"] for e in data.get("log", [])]],
                            format_func=lambda k: k[:8] or "(none)")
        notes = st.text_area("Notes")
        st.caption("Log entries can't be edited or deleted. To fix a mistake, add a correction.")
        if st.form_submit_button("Add entry", type="primary"):
            if api.safe(api.post, f"/maintenance/{eid}/log", {"kind": kind, "notes": notes,
                                                              "plan_id": plan or None,
                                                              "corrects_id": corr or None}):
                st.rerun()
    st.dataframe([{"When": e["created_at"][:16], "Kind": e["kind"], "Task": e["task"], "Notes": e["notes"],
                   "Corrects": (e["corrects_id"] or "")[:8], "By": e["created_by"][:8]}
                  for e in data.get("log", [])], hide_index=True, use_container_width=True)
    st.dataframe([{"Task": p["task"], "Every": f"{p['interval_hours']:g} h" if p.get("interval_hours")
                   else f"{p['interval_days']} days", "Due": "Yes" if p["due"] else "", "Status": p["status"]}
                  for p in data.get("plans", [])], hide_index=True, use_container_width=True)


# ------------------------------------------------------------------ reports


def reports_page() -> None:
    common.page_header("Reports", "report", "staff")
    reps = api.safe(api.get, "/reports") or []
    clusters: dict[str, list] = {}
    for r in reps:
        clusters.setdefault(r["cluster_id"] or r["id"], []).append(r)
    order = sorted(clusters.values(), key=lambda rs: {"incident": 0, "near_miss": 1}.get(rs[0]["kind"], 2))
    if not order:
        st.success("No open reports.")
    for rs in order:
        r = rs[0]
        with st.container(border=True, key=f"rep_{r['kind']}_{r['id']}"):
            label = {"incident": ":red-badge[:material/emergency: Incident]",
                     "near_miss": ":orange-badge[:material/warning: Near-miss]"}.get(r["kind"],
                                                                                     ":blue-badge[Feedback]")
            st.markdown(f"{label} **{r['equipment_id'] or 'Shop'}** ({len(rs)} report(s))")
            for x in rs:
                st.write(f"- {x['text']}")
            note = st.text_input("Resolution", key=f"rn_{r['id']}")
            new = st.selectbox("Set machine status", ["(leave)", *common.STATUS], key=f"rs_{r['id']}") \
                if r["equipment_id"] else "(leave)"
            if st.button("Resolve", key=f"rr_{r['id']}"):
                if api.safe(api.post, f"/reports/{r['id']}/resolve",
                            {"note": note, "set_status": None if new == "(leave)" else new}):
                    st.rerun()


# --------------------------------------------------------------- inventory


def inventory_admin_page() -> None:
    common.page_header("Inventory & orders", "inventory", "staff")
    items = api.safe(api.get, "/inventory") or []
    st.dataframe([{"Item": i["name"], "On hand": i["on_hand"], "Unit": i["unit"],
                   "Reorder at": i["reorder_point"], "Reorder qty": i["reorder_qty"],
                   "Supplier": i["supplier"], "Used (30 d)": i["used_last_30_days"],
                   "Days left": i["days_left"]} for i in items], hide_index=True, use_container_width=True)
    c1, c2 = st.columns(2)
    csv = api.safe(api.call, "GET", "/inventory/export", params={"fmt": "csv"})
    xlsx = api.safe(api.call, "GET", "/inventory/export", params={"fmt": "xlsx"})
    if csv:
        c1.download_button("Download CSV", csv, "inventory.csv", "text/csv")
    if xlsx:
        c2.download_button("Download Excel", xlsx, "inventory.xlsx")
    with st.expander("Add an item"), st.form("add_item"):
        a, b = st.columns(2)
        name = a.text_input("Name")
        unit = b.text_input("Unit", "each")
        start = a.number_input("Starting quantity", 0.0, 100000.0, 0.0)
        rp = b.number_input("Reorder at", 0.0, 100000.0, 0.0)
        rq = a.number_input("Reorder quantity", 0.0, 100000.0, 0.0)
        sup = b.text_input("Supplier")
        aliases = st.text_input("Other names (comma separated)")
        if st.form_submit_button("Add", type="primary"):
            if api.safe(api.post, "/inventory/items", {
                    "name": name, "unit": unit, "starting_qty": start, "reorder_point": rp,
                    "reorder_qty": rq, "supplier": sup,
                    "aliases": [x.strip() for x in aliases.split(",") if x.strip()]}):
                st.rerun()
    if items:
        with st.expander("Record a stock count"), st.form("count"):
            names = {i["id"]: i["name"] for i in items}
            iid = st.selectbox("Item", list(names), format_func=names.get)
            n = st.number_input("Counted", 0.0, 100000.0, 0.0)
            if st.form_submit_button("Save count"):
                api.safe(api.post, f"/inventory/items/{iid}/count", {"counted": n})
                st.rerun()
    st.subheader("Orders")
    for o in api.safe(api.get, "/inventory/orders", params={"status": "approved"}) or []:
        with st.container(border=True, key=f"order_{o['id']}"):
            st.markdown(f"**{o['supplier']}** (approved)")
            for line in o["items"]:
                st.write(f"- {line['qty']:g} {line['unit']} {line['name']}")
            if st.button("Mark received", key=f"recv_{o['id']}"):
                api.safe(api.post, f"/inventory/orders/{o['id']}/received")
                st.rerun()
    st.caption("New order drafts appear in Approvals.")


# -------------------------------------------------------------- floor plan


LAYOUT_FIX = {
    "LAY-BOUNDS": "Drag it back inside the room outline.",
    "LAY-ROTATION": "Rotate it to 0, 90, 180 or 270 degrees.",
    "LAY-CLEAR": "Move it out of the machine's shaded clearance zone (turn on \"Show clearance "
                 "zones\" to see the zones).",
    "LAY-EXT": "Move the machine closer to a fire extinguisher, or add one nearby (Add object → "
               "Safety item: extinguisher).",
    "LAY-EXT-LASER": "Laser cutters need an extinguisher very close. Move one next to it (Add "
                     "object → Safety item: extinguisher).",
    "LAY-EYEWASH": "Move the machine closer to the eyewash station, or add one nearby (Add object "
                   "→ Safety item: eyewash).",
    "LAY-FIRSTAID": "Add a first aid kit (Add object → Safety item: first_aid).",
    "LAY-EXIT-NONE": "Mark at least one exit (Add object → Safety item: exit).",
    "LAY-AISLE": "Clear a walkway from this machine to an exit; move whatever is blocking it.",
}


def _layout_problem(fnd: dict) -> None:
    """One layout problem as a coloured box: what is wrong, then how to fix it."""
    fix = LAYOUT_FIX.get(fnd["rule_id"], "")
    body = f"**{fnd['text']}**" + (f"  \nHow to fix: {fix}" if fix else "")
    if fnd["severity"] == "high":
        st.error(body, icon=":material/error:")
    else:
        st.warning(body, icon=":material/warning:")


def _remember_layout(pid: str, findings: list[dict]) -> None:
    """Keep the problems from before a change, to say what the change broke or fixed."""
    st.session_state[f"_layout_before_{pid}"] = [f["text"] for f in findings]


def _layout_change_banner(pid: str, findings: list[dict]) -> None:
    before = st.session_state.pop(f"_layout_before_{pid}", None)
    if before is None:
        return
    now = [f["text"] for f in findings]
    new = [f for f in findings if f["text"] not in before]
    fixed = [t for t in before if t not in now]
    if new:
        st.error(f"**Your change created {len(new)} new layout problem"
                 f"{'s' if len(new) > 1 else ''}.** Staff have been alerted. See the details "
                 "below and move things back, or fix them.", icon=":material/report:")
    elif fixed:
        st.success(f"Saved. Your change fixed {len(fixed)} layout problem"
                   f"{'s' if len(fixed) > 1 else ''}.", icon=":material/check_circle:")
    else:
        st.info("Saved. The change didn't add or fix any layout problems.",
                icon=":material/check:")


def floorplan_editor_page() -> None:
    common.page_header("Floor plan editor", "edit_location", "staff")
    plans = api.safe(api.get, "/floorplans") or []
    with st.expander("Upload a new floor plan" if plans else "Upload your floor plan", expanded=not plans):
        st.caption("Draw the empty room (no equipment) in Lucidchart, SmartDraw or similar and "
                   "export a PNG, or skip the file to start from a plain outline.")
        with st.form("new_plan"):
            name = st.text_input("Name", "Main floor")
            units = st.selectbox("Units", list(common.UNITS), index=1)
            a, b = st.columns(2)
            w = a.number_input(f"Room width ({units})", 1.0, 1000.0, 40.0)
            h = b.number_input(f"Room depth ({units})", 1.0, 1000.0, 30.0)
            f = st.file_uploader("Image", type=["png", "jpg", "jpeg", "pdf"])
            if st.form_submit_button("Create", type="primary"):
                files = {"file": (f.name, f.getvalue())} if f else None
                if api.safe(api.call, "POST", "/floorplans", data={"name": name, "width": w, "height": h,
                                                                    "units": units}, files=files):
                    st.rerun()
    if not plans:
        return
    names = {p["id"]: p["name"] for p in plans}
    pid = st.selectbox("Plan", list(names), format_func=names.get)
    plan = api.safe(api.get, f"/floorplans/{pid}")
    if not plan:
        return
    findings = plan["check"]["data"].get("findings", [])
    _layout_change_banner(pid, findings)
    if findings:
        serious = sum(1 for f in findings if f["severity"] == "high")
        st.subheader(f"Layout check: {len(findings)} problem{'s' if len(findings) > 1 else ''}"
                     + (f" ({serious} serious)" if serious else ""))
        for fnd in sorted(findings, key=lambda f: f["severity"] != "high"):
            _layout_problem(fnd)
    else:
        st.success("Layout check passed: clearances, exits and safety equipment are all fine.",
                   icon=":material/check_circle:")
    show_zones = st.toggle("Show clearance zones", True)
    ev = floorplan(plan, mode="edit", show_zones=show_zones, key=f"edit_fp_{pid}")
    if ev and ev.get("action") == "save" and st.session_state.get("_last_save") != ev.get("n"):
        st.session_state["_last_save"] = ev.get("n")
        _remember_layout(pid, findings)
        res = api.safe(api.post, f"/floorplans/{pid}/save", {"items": ev["items"]})
        if res:
            st.rerun()
        st.session_state.pop(f"_layout_before_{pid}", None)  # save failed: nothing changed
    t1, t2, t3 = st.tabs(["Add machine", "Add object", "Remove item"])
    with t1:
        if plan["unplaced"]:
            opts = common.equipment_options()
            with st.form("add_machine"):
                eid = st.selectbox("Machine", plan["unplaced"], format_func=lambda k: opts.get(k, k))
                if st.form_submit_button("Place it"):
                    _remember_layout(pid, findings)
                    api.safe(api.post, f"/floorplans/{pid}/items", {"kind": "equipment", "equipment_id": eid})
                    st.rerun()
            if st.button("Suggest safe spots for all unplaced machines", icon=":material/auto_awesome:"):
                r = api.safe(api.post, f"/floorplans/{pid}/suggest")
                if r:
                    st.info(f"{len(r['output'].get('proposals', []))} placement(s) suggested; review "
                            "them in Approvals.")
        else:
            st.write("Every machine is on the plan.")
    with t2, st.form("add_obj"):
        a, b, c = st.columns(3)
        kind = a.selectbox("Type", ["object", "safety"])
        shape = b.selectbox("Shape", ["rect", "circle", "oval", "triangle"])
        safety = c.selectbox("Safety item", ["", "extinguisher", "first_aid", "eyewash", "estop", "exit"])
        label = st.text_input("Label", "Cabinet")
        units = a.selectbox("Units ", list(common.UNITS), index=1)
        w = b.number_input("Width", 0.1, 500.0, 3.0)
        h = c.number_input("Depth", 0.1, 500.0, 2.0)
        if st.form_submit_button("Add"):
            _remember_layout(pid, findings)
            api.safe(api.post, f"/floorplans/{pid}/items", {
                "kind": kind, "shape": shape, "safety_type": safety or None, "label": label,
                "w": w, "h": h, "units": units, "x": 0.5, "y": 0.5})
            st.rerun()
    with t3:
        labels = {i["id"]: i.get("name") or i["label"] or i["kind"] for i in plan["items"]}
        rid = st.selectbox("Item", list(labels), format_func=labels.get)
        if st.button("Remove from plan"):
            _remember_layout(pid, findings)
            api.safe(api.delete, f"/floorplans/{pid}/items/{rid}")
            st.rerun()


# ----------------------------------------------------------------- settings


def settings_page() -> None:
    common.page_header("Settings & logs", "tune", "staff")
    s = api.safe(api.get, "/settings") or {}
    with st.form("settings"):
        name = st.text_input("Makerspace name", s.get("name", ""))
        num = st.text_input("Emergency number", s.get("emergency_number", "911"))
        note = st.text_area("Emergency note", s.get("emergency_note", ""))
        grace = st.number_input("Maintenance grace period (days)", 0, 60, s.get("maintenance_grace_days", 7))
        if st.form_submit_button("Save", type="primary"):
            res = api.safe(api.patch, "/settings", {"name": name, "emergency_number": num,
                                                   "emergency_note": note,
                                                   "maintenance_grace_days": int(grace)})
            if res:
                st.session_state["site"] = res
                st.success("Saved")
    st.subheader("LLM use")
    st.dataframe(api.safe(api.get, "/telemetry/llm") or [], hide_index=True, use_container_width=True)
    st.subheader("Audit log")
    st.dataframe([{"When": a["created_at"][:19], "Who": a["actor"][:10], "Action": a["action"],
                   "Target": f"{a['target_type']}:{a['target_id'][:10]}"}
                  for a in api.safe(api.get, "/audit") or []], hide_index=True, use_container_width=True)
