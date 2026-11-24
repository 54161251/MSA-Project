"""Member portal pages."""

from __future__ import annotations

import streamlit as st

from msa.ui import api, common
from msa.ui.floorplan import floorplan


def pages() -> list:
    training = st.Page(training_page, title="My training", icon=":material/school:", url_path="training")
    st.session_state["_training_page"] = training
    return [
        st.Page(equipment_page, title="Equipment", icon=":material/precision_manufacturing:",
                url_path="equipment", default=st.session_state["user"]["role"] != "staff"),
        training,
        st.Page(ask_page, title="Ask a question", icon=":material/chat:", url_path="ask"),
        st.Page(project_page, title="Check my project", icon=":material/fact_check:", url_path="project"),
        st.Page(report_page, title="Report an issue", icon=":material/report:", url_path="report"),
        st.Page(inventory_page, title="Supplies", icon=":material/inventory_2:", url_path="supplies"),
        st.Page(floorplan_page, title="Floor plan", icon=":material/map:", url_path="map"),
    ]


# ------------------------------------------------------------------ equipment


def equipment_page() -> None:
    common.page_header("Equipment", "precision_manufacturing", "member")
    eqs = api.safe(api.get, "/equipment") or []
    if not eqs:
        st.info("No equipment yet.")
        return
    names = {e["id"]: e["name"] for e in eqs}
    choice = st.selectbox("Machine", list(names), format_func=names.get,
                          index=list(names).index(st.session_state.get("eq_focus", list(names)[0]))
                          if st.session_state.get("eq_focus") in names else 0)
    equipment_detail(choice)


def equipment_detail(eid: str) -> None:
    data = api.safe(api.get, f"/equipment/{eid}")
    if not data:
        return
    eq = data["equipment"]
    st.header(eq["name"])
    st.markdown(f"{common.badge('status', eq['status'])} &nbsp; "
                f"{common.badge('training', data['my_training'])}")
    if eq["status"] != "available" and eq.get("status_reason"):
        st.warning(eq["status_reason"], icon=":material/warning:")
    can = api.safe(api.get, f"/tools/can-use/{eid}") or {}
    c1, c2, c3 = st.columns(3)
    with c1:
        if can.get("verdict") == "yes":
            st.success("You can use this machine.", icon=":material/check_circle:")
        else:
            st.info("Not yet: " + "; ".join(can.get("reasons", [])), icon=":material/lock:")
    with c2:
        if data["my_training"] in ("not_started", "expired"):
            if st.button("Take the quiz", icon=":material/quiz:", key=f"quiz_{eid}"):
                st.session_state["quiz_eq"] = eid
                st.switch_page(st.session_state["_training_page"])
    with c3:
        if can.get("verdict") == "yes":
            u1, u2 = st.columns(2)
            if u1.button("Start session", key=f"start_{eid}"):
                if api.safe(api.post, "/usage/start", {"equipment_id": eid}):
                    st.toast("Session started")
            if u2.button("End session", key=f"stop_{eid}"):
                if api.safe(api.post, "/usage/stop", {}):
                    st.toast("Session ended")
    ppe = api.safe(api.get, f"/tools/ppe/{eid}") or {}
    if ppe.get("data", {}).get("items"):
        st.subheader("Before you use it")
        for it in ppe["data"]["items"]:
            st.markdown(f"- {it['item']}")
    docs = {d["type"]: d for d in data["documents"] if d["status"] == "approved"}
    tabs = st.tabs(["Procedure (SOP)", "Training guide", "Materials", "Specs"])
    for tab, dtype in zip(tabs[:3], ["sop", "training", "materials"], strict=True):
        with tab:
            if dtype not in docs:
                st.info("Not published yet.")
                continue
            full = api.safe(api.get, f"/documents/{docs[dtype]['id']}") or {}
            common.statements_view(full.get("statements", []))
            st.caption("Hover a line to see which page of the manual it comes from.")
    with tabs[3]:
        st.json(eq.get("specs") or {}, expanded=True)
        for link in eq.get("video_links") or []:
            st.markdown(f"Training video: {link}")


# ------------------------------------------------------------------- training


def training_page() -> None:
    common.page_header("My training", "school", "member")
    u = st.session_state["user"]
    rec = api.safe(api.get, f"/users/{u['id']}") or {"training": []}
    rows = [{"Machine": t["equipment_name"], "Status": common.TRAINING.get(t["state"], ("", t["state"]))[1],
             "Expires": (t["expires_at"] or "")[:10],
             "Quiz score": f"{t['quiz_score']:.0%}" if t["quiz_score"] is not None else ""}
            for t in rec["training"]]
    st.dataframe(rows, hide_index=True, use_container_width=True)
    st.subheader("Take a quiz")
    opts = {t["equipment_id"]: t["equipment_name"] for t in rec["training"]
            if t["state"] in ("not_started", "expired")}
    if not opts:
        st.info("No quizzes to take right now.")
        return
    focus = st.session_state.get("quiz_eq")
    eid = st.selectbox("Machine", list(opts), format_func=opts.get,
                       index=list(opts).index(focus) if focus in opts else 0)
    if st.button("Start quiz", type="primary"):
        q = api.safe(api.post, f"/quiz/{eid}/start")
        if q:
            st.session_state["quiz_data"] = q
            st.session_state.pop("quiz_result", None)
    q = st.session_state.get("quiz_data")
    if q:
        with st.form("quiz_form"):
            answers = {}
            for i, item in enumerate(q["questions"], start=1):
                choice = st.radio(f"{i}. {item['prompt']}", range(len(item["options"])),
                                  format_func=lambda k, it=item: it["options"][k], index=None,
                                  key=f"q_{item['id']}")
                if choice is not None:
                    answers[item["id"]] = choice
            if st.form_submit_button("Submit answers", type="primary"):
                if len(answers) < len(q["questions"]):
                    st.warning("Please answer every question.")
                else:
                    st.session_state["quiz_result"] = api.safe(
                        api.post, f"/quiz/attempts/{q['attempt_id']}/submit", {"answers": answers})
                    st.session_state.pop("quiz_data", None)
                    st.rerun()
    res = st.session_state.get("quiz_result")
    if res:
        (st.success if res["passed"] else st.error)(
            f"Score {res['score']:.0%} (pass mark {res['pass_mark']:.0%}). {res.get('next', '')}")
        for r in res["review"]:
            mark = ":material/check:" if r["correct"] else ":material/close:"
            st.markdown(f"{mark} {r['prompt']}")
            if not r["correct"]:
                common.citations(r["citations"], "Where to read about this")


# ------------------------------------------------------------------------ ask


def ask_page() -> None:
    common.page_header("Ask a question", "chat", "member")
    st.caption("Answers come only from the makerspace's approved documents. If there's no "
               "approved answer, or the question is risky, you'll be sent to a staff member.")
    hist = st.session_state.setdefault("ask_history", [])
    opts = common.equipment_options(include_none=True)
    eid = st.selectbox("About which machine? (optional)", list(opts), format_func=opts.get)
    for m in hist:
        with st.chat_message(m["role"]):
            render_answer(m) if m["role"] == "assistant" else st.write(m["text"])
    q = st.chat_input("Type your question")
    if q:
        hist.append({"role": "user", "text": q})
        res = api.safe(api.post, "/ask", {"question": q, "equipment_id": eid or None})
        if res:
            hist.append({"role": "assistant", **res})
        st.rerun()


def render_answer(m: dict) -> None:
    out = m.get("output", {})
    if out.get("emergency"):
        common.emergency_block(out)
        return
    if m.get("status") == "refused":
        st.warning(out.get("answer", ""), icon=":material/support_agent:")
    else:
        st.write(out.get("answer", ""))
    for w in out.get("warnings", []):
        st.warning(w, icon=":material/warning:")
    common.citations(m.get("citations", []))


# -------------------------------------------------------------- project check


def project_page() -> None:
    common.page_header("Check my project", "fact_check", "member")
    st.caption("Describe what you want to make and how. The check looks at every step: "
               "machines, materials, your training, and risky combinations.")
    with st.form("pc"):
        text = st.text_area("Your project", placeholder="Laser cut a box from 3 mm plywood, then "
                            "glue it together and paint it.", height=140)
        go = st.form_submit_button("Check it", type="primary")
    if go and text.strip():
        st.session_state["pc_result"] = api.safe(api.post, "/project-checks", {"description": text})
    res = st.session_state.get("pc_result")
    if not res:
        return
    out = res["output"]
    verdict = out.get("verdict")
    if verdict == "clear":
        st.success(out["message"], icon=":material/check_circle:")
    elif verdict == "clear_with_conditions":
        st.info(out["message"], icon=":material/checklist:")
    else:
        # Waiting for a staff decision: yellow, like everything else that waits on staff.
        st.warning(out.get("message", "A staff member needs to review this."),
                   icon=":material/support_agent:")
    steps = out.get("steps", [])
    general = [f for f in out.get("findings", []) if f["step"] is None]
    for f in general:
        st.markdown(f"{common.SEVERITY_ICON.get(f['severity'], '')} {f['text']}")
    for i, s in enumerate(steps):
        with st.container(border=True, key=f"step_{i}"):
            st.markdown(f"**Step {i + 1}: {s['text']}**")
            for f in [f for f in out.get("findings", []) if f["step"] == i]:
                st.markdown(f"{common.SEVERITY_ICON.get(f['severity'], '')} {f['text']}")
                common.citations(f.get("citations", []))


# --------------------------------------------------------------------- report


def report_page() -> None:
    common.page_header("Report an issue", "report", "member")
    st.markdown("<div class='msa-emergency'><span class='msa-icon'>emergency</span>"
                "If someone is hurt or there's a fire, call "
                f"{(st.session_state.get('site') or {}).get('emergency_number', '911')} first.</div>",
                unsafe_allow_html=True)
    opts = common.equipment_options(include_none=True)
    with st.form("report"):
        kind = st.radio("What happened?", ["feedback", "near_miss", "incident"], horizontal=True,
                        format_func={"feedback": "Something's not working / missing / running low",
                                     "near_miss": "Something almost went wrong",
                                     "incident": "Someone got hurt, or fire / smoke"}.get)
        eid = st.selectbox("Machine (optional)", list(opts), format_func=opts.get)
        text = st.text_area("Describe it")
        sent = st.form_submit_button("Send report", type="primary")
    if sent and text.strip():
        res = api.safe(api.post, "/reports", {"text": text, "equipment_id": eid or None, "kind": kind})
        if res:
            out = res["output"]
            if out.get("emergency"):
                common.emergency_block(out)
            else:
                st.success(out.get("message", "Thanks, staff have been notified."))
                if out.get("suggestion"):
                    st.info("This might help: " + out["suggestion"]["text"])
                    common.citations(out["suggestion"]["citations"])


# ------------------------------------------------------------------ inventory


def inventory_page() -> None:
    common.page_header("Supplies", "inventory_2", "member")
    staff = (st.session_state.get("user") or {}).get("role") == "staff"
    st.caption('Tell us what you used, in your own words: "used two sheets of 3 mm acrylic".'
               + ("" if staff else " Only staff can add stock; to give supplies to the "
                  "makerspace, hand them to a staff member."))
    with st.form("use"):
        text = st.text_input("What did you use or add?" if staff else "What did you use?")
        go = st.form_submit_button("Log it", type="primary")
    if go and text.strip():
        st.session_state["inv_res"] = api.safe(api.post, "/inventory/use", {"text": text})
    res = st.session_state.get("inv_res")
    if res:
        out = res["output"]
        if out.get("needs_clarification"):
            st.warning(out["message"])
            for c in out.get("options", []):
                if st.button(f"{c['name']}", key=f"pick_{c['item_id']}"):
                    q = out.get("quantity")
                    if not q:
                        st.warning("Please include how many.")
                    else:
                        st.session_state["inv_res"] = api.safe(api.post, "/inventory/use", {
                            "item_id": c["item_id"], "quantity": q, "direction": out.get("direction", -1),
                            "text": "picked from options"})
                        st.rerun()
        elif res.get("status") == "ok":
            st.success(f"Logged {out['delta']:+g} {out['unit']} of {out['item']}. "
                       f"{out['on_hand']:g} left.")
        elif out.get("message"):  # e.g. a member trying to add stock
            st.warning(out["message"], icon=":material/block:")
    items = api.safe(api.get, "/inventory") or []
    st.dataframe([{"Item": i["name"], "On hand": i["on_hand"], "Unit": i["unit"],
                   "Low?": "Yes" if i["on_hand"] <= i["reorder_point"] else ""} for i in items],
                 hide_index=True, use_container_width=True)


# ------------------------------------------------------------------ floor plan


def floorplan_page() -> None:
    common.page_header("Floor plan", "map", "member")
    plans = api.safe(api.get, "/floorplans") or []
    if not plans:
        st.info("No floor plan yet.")
        return
    plan = api.safe(api.get, f"/floorplans/{plans[0]['id']}")
    if not plan:
        return
    ev = floorplan(plan, mode="view", show_zones=False, key="member_fp")
    st.caption("Machine colours also have text labels. Exits, extinguishers, first aid and "
               "eyewash are marked.")
    if ev and ev.get("action") == "click":
        it = next((i for i in plan["items"] if i["id"] == ev["id"]), None)
        if it and it.get("equipment_id"):
            equipment_detail(it["equipment_id"])
        elif it:
            st.info(f"{it['label'] or it['kind']}: {it.get('safety_type') or it['shape']}")
    with st.expander("List view (for screen readers)"):
        for i in plan["items"]:
            st.write(f"{i.get('name') or i['label']}: {i.get('status', i.get('safety_type') or i['kind'])}")
