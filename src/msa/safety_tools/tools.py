"""The deterministic safety tools (Assumptions doc C7, R3 tool list).

Each takes plain arguments plus an open session and returns a ToolVerdict:
    {"verdict": ..., "reasons": [...], "rule_ids": [...], "data": {...}}
Agents must call these for any check they cover and never reimplement them (C7).
"""

from __future__ import annotations

import math
from collections import deque
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from msa.core import training
from msa.core.auth import ADULT_AGE, age_of
from msa.core.contracts import USABLE_STATUSES, WARNING_STATUSES, ToolVerdict
from msa.core.docs import approved_docs
from msa.core.models import Equipment, FloorPlan, MaterialRule, PlacedItem, User
from msa.safety_tools.rules import canonical_material, category_label, parent_material, rules

# --------------------------------------------------------------- materials


def check_material(db: Session, equipment_id: str, material: str) -> ToolVerdict:
    """allowed | banned | ask_staff | unknown (unknown => E01; never defaults to safe, G3)."""
    eq = db.get(Equipment, equipment_id)
    if eq is None:
        return ToolVerdict(verdict="unknown", reasons=[f"Equipment {equipment_id} is not registered"],
                           rule_ids=["EQ-UNKNOWN"], data={"codes": ["E02"]})
    canon = canonical_material(material)
    if canon is None:
        return ToolVerdict(
            verdict="unknown",
            reasons=[f"'{material}' is not a material the system recognizes; ask staff."],
            rule_ids=["MAT-UNKNOWN"], data={"codes": ["E01"], "material": material},
        )
    for ban in rules()["banned"].get(eq.category, []):
        if ban["material"] == canon:
            return ToolVerdict(verdict="banned", reasons=[ban["reason"]], rule_ids=[ban["rule_id"]],
                               data={"material": canon, "scope": "category"})
    rule = db.scalar(select(MaterialRule).where(MaterialRule.equipment_id == eq.id,
                                                MaterialRule.material == canon))
    parent = parent_material(canon)
    if rule is None and parent:
        rule = db.scalar(select(MaterialRule).where(MaterialRule.equipment_id == eq.id,
                                                    MaterialRule.material == parent))
    if rule is None:
        return ToolVerdict(
            verdict="unknown",
            reasons=[f"{canon.replace('_', ' ')} isn't on the material list for {eq.name}; ask staff."],
            rule_ids=["MAT-NOT-LISTED"], data={"codes": ["E01"], "material": canon},
        )
    return ToolVerdict(verdict=rule.verdict, reasons=[rule.reason] if rule.reason else [],
                       rule_ids=[f"MAT-{eq.id}-{canon}"],
                       data={"material": canon, "source_ref": rule.source_ref, "scope": "machine"})


# --------------------------------------------------------------------- PPE


def required_ppe(db: Session, equipment_id: str) -> ToolVerdict:
    eq = db.get(Equipment, equipment_id)
    if eq is None:
        return ToolVerdict(verdict="unknown", reasons=["Equipment not registered"],
                           rule_ids=["EQ-UNKNOWN"], data={"codes": ["E02"]})
    items: list[dict[str, Any]] = []
    rule_ids = []
    for b in rules()["baseline_ppe"].get(eq.category, []):
        items.append({"item": b["item"], "source": "house rule", "citations": []})
        rule_ids.append(b["rule_id"])
    sops = approved_docs(db, eq.id, ["sop"])
    if not sops:
        return ToolVerdict(verdict="unknown", reasons=[f"{eq.name} has no approved SOP"],
                           rule_ids=rule_ids + ["SOP-MISSING"],
                           data={"codes": ["E02"], "items": items})
    for st in sops[0].statements:
        if st.get("kind") == "ppe":
            items.append({"item": st["text"], "source": "sop", "citations": st.get("citations", [])})
    return ToolVerdict(verdict="ok", reasons=[], rule_ids=rule_ids,
                       data={"items": items, "sop_id": sops[0].id})


# --------------------------------------------------------------- permission


def can_use(db: Session, user_id: str, equipment_id: str) -> ToolVerdict:
    """yes | no. Certified + prerequisites certified + machine usable + waiver valid (C6)."""
    reasons, rules_hit, codes = [], [], []
    u = db.get(User, user_id)
    eq = db.get(Equipment, equipment_id)
    if u is None or eq is None:
        return ToolVerdict(verdict="no", reasons=["Unknown user or equipment"],
                           rule_ids=["USE-UNKNOWN"], data={"codes": ["E02"]})
    warnings = []
    if eq.status not in USABLE_STATUSES:
        reasons.append(f"{eq.name} is {eq.status.replace('_', ' ')}")
        rules_hit.append("USE-STATUS")
        codes.append("E06")
    elif eq.status in WARNING_STATUSES:
        warnings.append(f"{eq.name}: {eq.status.replace('_', ' ')}. {eq.status_reason}".strip())
    if not approved_docs(db, eq.id, ["sop"]):
        reasons.append(f"{eq.name} has no approved SOP yet")
        rules_hit.append("USE-NO-SOP")
        codes.append("E02")
    if u.role != "staff":
        if age_of(u.birth_year) < ADULT_AGE and u.waiver_status != "guardian_signed":
            reasons.append("A guardian-signed waiver is required")
            rules_hit.append("USE-WAIVER")
            codes.append("E13")
        state = training.current_state(db, u.id, eq.id)
        if state != "certified":
            reasons.append(f"Training for {eq.name} is {state.replace('_', ' ')}")
            rules_hit.append("USE-TRAINING")
            codes.append("E05")
        for pre in eq.prerequisites or []:
            if training.current_state(db, u.id, pre) != "certified":
                pre_eq = db.get(Equipment, pre)
                reasons.append(f"Prerequisite training needed: {pre_eq.name if pre_eq else pre}")
                rules_hit.append("USE-PREREQ")
                codes.append("E05")
    verdict = "no" if reasons else "yes"
    return ToolVerdict(verdict=verdict, reasons=reasons, rule_ids=rules_hit,
                       data={"codes": sorted(set(codes)), "warnings": warnings})


# ---------------------------------------------------------------- SOP rules


def check_sop_rules(category: str, statements: list[dict[str, Any]]) -> ToolVerdict:
    """Structure checks for an SOP draft (used by Review, E09)."""
    r = rules()
    reasons, rule_ids = [], []
    present = {s.get("section", "").strip().lower() for s in statements}
    for sec in r["sop_required_sections"]:
        if sec.lower() not in present:
            reasons.append(f"Missing required section: {sec}")
            rule_ids.append(f"SOP-SECTION-{sec.upper().replace(' ', '-')}")
    ppe_text = " ".join(s["text"].lower() for s in statements if s.get("kind") == "ppe")
    if not ppe_text:
        reasons.append("No PPE statements")
        rule_ids.append("SOP-PPE-NONE")
    for group in r["sop_required_ppe_terms"].get(category, []):
        if not any(term in ppe_text for term in group):
            reasons.append(f"PPE must mention one of: {', '.join(group)}")
            rule_ids.append(f"SOP-PPE-{group[0].upper()}")
    banned = {b["material"]: b for b in r["banned"].get(category, [])}
    for s in statements:
        if s.get("kind") != "material":
            continue
        low = s["text"].lower()
        if not any(w in low for w in ("allowed", "approved", "ok to", "can be cut", "safe to")):
            continue
        if any(w in low for w in ("never", "not allowed", "banned", "do not", "prohibited")):
            continue
        for canon in _materials_in(low):
            if canon in banned:
                reasons.append(f"Banned material listed as allowed: {canon} ({banned[canon]['reason']})")
                rule_ids.append(banned[canon]["rule_id"])
    return ToolVerdict(verdict="fail" if reasons else "pass", reasons=reasons, rule_ids=rule_ids)


def _materials_in(text: str) -> list[str]:
    from msa.safety_tools.rules import find_materials

    clause = text.split(". ")[0]
    return find_materials(clause) or ([c] if (c := canonical_material(clause)) else [])


# -------------------------------------------------------------- risk matrix


def risk_score(likelihood: int, severity: int) -> ToolVerdict:
    if not (1 <= likelihood <= 5 and 1 <= severity <= 5):
        return ToolVerdict(verdict="invalid", reasons=["likelihood and severity must be 1-5"],
                           rule_ids=["RISK-RANGE"])
    score = likelihood * severity
    level = next(lv["level"] for lv in rules()["risk_levels"] if score <= lv["max"])
    return ToolVerdict(verdict=level, rule_ids=["RISK-5X5"], data={"score": score})


# ------------------------------------------------------------------ layout


def _footprint(it: PlacedItem) -> tuple[float, float, float, float]:
    return it.x, it.y, it.x + it.w, it.y + it.h


def _clearance_rect(it: PlacedItem, eq: Equipment) -> tuple[float, float, float, float]:
    """Footprint grown by the machine's clearance. 'front' faces +y at rotation 0."""
    c = {"front": 0.0, "back": 0.0, "left": 0.0, "right": 0.0, **(eq.clearance or {})}
    rot = int(round((it.rotation or 0) / 90.0)) % 4
    # plan directions clockwise on screen (y grows downward): down, left, up, right.
    # At rotation 0 the sides face: front=down, left=left, back=up, right=right.
    order = ["front", "left", "back", "right"]
    down, left, up, right = (c[order[(d - rot) % 4]] for d in range(4))
    x0, y0, x1, y1 = _footprint(it)
    return x0 - left, y0 - up, x1 + right, y1 + down


def _overlap(a, b) -> bool:
    return a[0] < b[2] - 1e-9 and b[0] < a[2] - 1e-9 and a[1] < b[3] - 1e-9 and b[1] < a[3] - 1e-9


def _center(r) -> tuple[float, float]:
    return (r[0] + r[2]) / 2, (r[1] + r[3]) / 2


def check_layout(db: Session, plan_id: str) -> ToolVerdict:
    """Clearance overlaps, exit paths, extinguisher / first-aid / eyewash distances (E11)."""
    plan = db.get(FloorPlan, plan_id)
    if plan is None:
        return ToolVerdict(verdict="unknown", reasons=["No such floor plan"], rule_ids=["LAY-PLAN"])
    items = list(db.scalars(select(PlacedItem).where(PlacedItem.plan_id == plan.id)).all())
    eqs = {e.id: e for e in db.scalars(select(Equipment)).all()}
    return evaluate_layout(plan, items, eqs)


def evaluate_layout(plan: FloorPlan, items: list[PlacedItem], eqs: dict[str, Equipment]) -> ToolVerdict:
    """Pure version of check_layout: works on unsaved items too (used for layout suggestions)."""
    cfg = rules()["layout"]
    findings: list[dict[str, Any]] = []

    def add(rule_id: str, text: str, item_ids: list[str], severity: str = "warning"):
        findings.append({"rule_id": rule_id, "text": text, "item_ids": item_ids,
                         "severity": severity})

    room = (0.0, 0.0, plan.width_m, plan.height_m)
    for it in items:
        x0, y0, x1, y1 = _footprint(it)
        if x0 < -1e-9 or y0 < -1e-9 or x1 > room[2] + 1e-9 or y1 > room[3] + 1e-9:
            add("LAY-BOUNDS", f"{it.label or it.kind} sits outside the room outline", [it.id])

    machines = [it for it in items if it.kind == "equipment" and it.equipment_id in eqs]
    for m in machines:
        eq = eqs[m.equipment_id]
        zone = _clearance_rect(m, eq)
        for other in items:
            if other.id == m.id or other.kind == "safety":
                continue  # wall-mounted safety gear may sit beside a machine
            if _overlap(zone, _footprint(other)):
                add("LAY-CLEAR", f"{other.label or other.kind} is inside the clearance zone of "
                    f"{eq.name}", [m.id, other.id])

    safety = [it for it in items if it.kind == "safety"]

    def nearest(kind: str, pt) -> float:
        ds = [math.dist(pt, _center(_footprint(s))) for s in safety if s.safety_type == kind]
        return min(ds) if ds else math.inf

    for m in machines:
        eq = eqs[m.equipment_id]
        c = _center(_footprint(m))
        d = nearest("extinguisher", c)
        is_laser = eq.category == "laser_cutter"
        limit = cfg["laser_extinguisher_max_m"] if is_laser else cfg["extinguisher_max_m"]
        rid = "LAY-EXT-LASER" if is_laser else "LAY-EXT"
        if d > limit:
            add(rid, f"{eq.name}: nearest extinguisher is "
                f"{'missing' if d == math.inf else f'{d:.1f} m away'} (limit {limit} m)", [m.id],
                "high")
        if eq.category in cfg["eyewash_categories"]:
            d = nearest("eyewash", c)
            if d > cfg["eyewash_max_m"]:
                add("LAY-EYEWASH", f"{eq.name}: eyewash station "
                    f"{'missing' if d == math.inf else f'{d:.1f} m away'} "
                    f"(limit {cfg['eyewash_max_m']} m)", [m.id], "high")
    if cfg.get("require_first_aid") and not any(s.safety_type == "first_aid" for s in safety):
        add("LAY-FIRSTAID", "No first aid kit is placed on the floor plan", [], "high")

    exits = list(plan.exits or []) + [
        {"x": _center(_footprint(s))[0], "y": _center(_footprint(s))[1]}
        for s in safety if s.safety_type == "exit"
    ]
    if not exits:
        add("LAY-EXIT-NONE", "No exits are marked on the floor plan", [], "high")
    elif machines:
        blocked = _unreachable_machines(plan, items, machines, exits, cfg)
        for m in blocked:
            add("LAY-AISLE", f"{eqs[m.equipment_id].name} has no clear path at least "
                f"{cfg['min_aisle_m']} m wide to an exit", [m.id], "high")
    verdict = "fail" if any(f["severity"] == "high" for f in findings) else (
        "warn" if findings else "pass")
    return ToolVerdict(verdict=verdict, reasons=[f["text"] for f in findings],
                       rule_ids=sorted({f["rule_id"] for f in findings}),
                       data={"findings": findings, "codes": ["E11"] if findings else []})


def _unreachable_machines(plan, items, machines, exits, cfg) -> list[PlacedItem]:
    """Grid BFS: a person (aisle-width disc) must get from beside each machine to an exit."""
    g = cfg["grid_m"]
    nx, ny = max(1, int(math.ceil(plan.width_m / g))), max(1, int(math.ceil(plan.height_m / g)))
    half = cfg["min_aisle_m"] / 2
    blocked = [[False] * ny for _ in range(nx)]
    for it in items:
        if it.kind == "safety" and it.safety_type in ("exit", "extinguisher", "first_aid", "eyewash",
                                                       "estop"):
            continue
        x0, y0, x1, y1 = _footprint(it)
        for i in range(max(0, int((x0 - half) / g)), min(nx, int(math.ceil((x1 + half) / g)))):
            for j in range(max(0, int((y0 - half) / g)), min(ny, int(math.ceil((y1 + half) / g)))):
                blocked[i][j] = True
    # cells within half an aisle of a wall are not walkable either
    edge = int(math.ceil(half / g))
    for i in range(nx):
        for j in range(ny):
            if i < edge or j < edge or i >= nx - edge or j >= ny - edge:
                blocked[i][j] = True
    seen = [[False] * ny for _ in range(nx)]
    q: deque = deque()
    for ex in exits:
        ci = min(nx - 1, max(0, int(ex["x"] / g)))
        cj = min(ny - 1, max(0, int(ex["y"] / g)))
        # step inward from the exit until we reach a walkable cell
        best = None
        for r in range(0, edge + int(1.5 / g)):
            for i in range(max(0, ci - r), min(nx, ci + r + 1)):
                for j in range(max(0, cj - r), min(ny, cj + r + 1)):
                    if not blocked[i][j]:
                        best = (i, j)
                        break
                if best:
                    break
            if best:
                break
        if best and not seen[best[0]][best[1]]:
            seen[best[0]][best[1]] = True
            q.append(best)
    while q:
        i, j = q.popleft()
        for di, dj in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            a, b = i + di, j + dj
            if 0 <= a < nx and 0 <= b < ny and not seen[a][b] and not blocked[a][b]:
                seen[a][b] = True
                q.append((a, b))
    out = []
    for m in machines:
        x0, y0, x1, y1 = _footprint(m)
        ring = int(math.ceil((half + g) / g)) + 1
        i0, i1 = int(x0 / g) - ring, int(math.ceil(x1 / g)) + ring
        j0, j1 = int(y0 / g) - ring, int(math.ceil(y1 / g)) + ring
        ok = any(seen[i][j] for i in range(max(0, i0), min(nx, i1))
                 for j in range(max(0, j0), min(ny, j1)))
        if not ok:
            out.append(m)
    return out


def describe_category(cat: str) -> str:
    return category_label(cat)
