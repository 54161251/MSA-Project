"""Layout agent: suggests where unplaced machines could go.

The search is deterministic: try positions on a 0.25 m grid (both orientations), keep those
whose clearance zone fits in the room and touches nothing, and prefer spots against a wall
and, for lasers and resin printers, near the extinguisher or eyewash they need. Every
candidate layout is then run through the R3 layout check (msa.safety_tools.evaluate_layout),
so a suggestion never introduces a new high-severity problem. Staff accept or reject the
suggestion as a `layout_change` approval (G1).
"""

from __future__ import annotations

import math
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from msa.core.contracts import AgentResult
from msa.core.models import Equipment, FloorPlan, PlacedItem
from msa.core.registry import registry
from msa.core.services import request_approval
from msa.safety_tools.rules import rules
from msa.safety_tools.tools import _clearance_rect, _footprint, _overlap, evaluate_layout

STEP = 0.25


def _zone(it: PlacedItem, eq: Equipment | None):
    return _clearance_rect(it, eq) if eq else _footprint(it)


def _fits(cand: PlacedItem, eq: Equipment, plan: FloorPlan, placed: list[PlacedItem],
          eqs: dict[str, Equipment]) -> bool:
    zone = _zone(cand, eq)
    if zone[0] < 0 or zone[1] < 0 or zone[2] > plan.width_m or zone[3] > plan.height_m:
        return False
    foot = _footprint(cand)
    for other in placed:
        if other.kind == "safety":
            continue
        ofoot = _footprint(other)
        if _overlap(zone, ofoot):
            return False
        oeq = eqs.get(other.equipment_id or "")
        if oeq and _overlap(foot, _zone(other, oeq)):
            return False
    return True


def _score(cand: PlacedItem, eq: Equipment, plan: FloorPlan, placed: list[PlacedItem]) -> float:
    x0, y0, x1, y1 = _footprint(cand)
    wall = min(x0, y0, plan.width_m - x1, plan.height_m - y1)
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    cfg = rules()["layout"]
    need = None
    if eq.category == "laser_cutter":
        need = ("extinguisher", cfg["laser_extinguisher_max_m"])
    elif eq.category in cfg["eyewash_categories"]:
        need = ("eyewash", cfg["eyewash_max_m"])
    penalty = 0.0
    if need:
        ds = [math.dist((cx, cy), ((s.x + s.w / 2), (s.y + s.h / 2))) for s in placed
              if s.kind == "safety" and s.safety_type == need[0]]
        d = min(ds) if ds else math.inf
        penalty = 0 if d <= need[1] else (100 if d == math.inf else d)
    return wall + penalty


def suggest_positions(db: Session, plan: FloorPlan, equipment_ids: list[str] | None = None) -> dict[str, Any]:
    placed = list(db.scalars(select(PlacedItem).where(PlacedItem.plan_id == plan.id)).all())
    eqs = {e.id: e for e in db.scalars(select(Equipment)).all()}
    on_plan = {p.equipment_id for p in placed if p.equipment_id}
    todo = [eqs[i] for i in (equipment_ids or [e for e in eqs if e not in on_plan])
            if i in eqs and i not in on_plan and eqs[i].status != "retired"]
    todo.sort(key=lambda e: -(e.footprint_w * e.footprint_d))  # big machines first
    base = evaluate_layout(plan, placed, eqs)
    base_high = {(f["rule_id"], tuple(f["item_ids"])) for f in base.data.get("findings", [])
                 if f["severity"] == "high"}
    proposals, unplaced = [], []
    for eq in todo:
        cands = []
        for rot, (w, h) in ((0, (eq.footprint_w, eq.footprint_d)), (90, (eq.footprint_d, eq.footprint_w))):
            y = 0.0
            while y + h <= plan.height_m + 1e-9:
                x = 0.0
                while x + w <= plan.width_m + 1e-9:
                    c = PlacedItem(plan_id=plan.id, equipment_id=eq.id, kind="equipment",
                                   label=eq.name, x=round(x, 2), y=round(y, 2), w=w, h=h, rotation=rot)
                    if _fits(c, eq, plan, placed, eqs):
                        cands.append((_score(c, eq, plan, placed), c))
                    x += STEP
                y += STEP
        cands.sort(key=lambda t: t[0])
        chosen = None
        for _, c in cands[:40]:
            v = evaluate_layout(plan, placed + [c], eqs)
            new_high = [f for f in v.data.get("findings", []) if f["severity"] == "high"
                        and (f["rule_id"], tuple(f["item_ids"])) not in base_high]
            if not new_high:
                chosen = c
                break
        if chosen is None:
            unplaced.append(eq.id)
            continue
        placed.append(chosen)
        proposals.append({"equipment_id": eq.id, "label": eq.name, "x": chosen.x, "y": chosen.y,
                          "w": chosen.w, "h": chosen.h, "rotation": chosen.rotation})
    final = evaluate_layout(plan, placed, eqs)
    return {"proposals": proposals, "unplaced": unplaced, "check": final.model_dump()}


@registry.agent("layout")
def run(db: Session, payload: dict, actor: str) -> AgentResult:
    plan = db.get(FloorPlan, payload.get("plan_id", ""))
    if plan is None:
        return AgentResult(agent="layout", status="error", output={"error": "no such floor plan"})
    out = suggest_positions(db, plan, payload.get("equipment_ids"))
    if not out["proposals"]:
        return AgentResult(agent="layout", status="ok", output={**out, "message":
                           "Nothing to place, or no safe spot found."})
    a = request_approval(db, "layout_change", plan.id, {"proposals": out["proposals"]}, "layout",
                         f"Place {len(out['proposals'])} machine(s) on {plan.name}")
    return AgentResult(agent="layout", status="needs_approval", approvals=[a.id], output=out,
                       escalations=["E11"] if out["check"]["data"].get("findings") else [],
                       trace=[f"searched placements for {len(out['proposals']) + len(out['unplaced'])} "
                              "machine(s)"])


def apply_proposals(db: Session, plan_id: str, proposals: list[dict], actor: str) -> int:
    n = 0
    for p in proposals:
        exists = db.scalar(select(PlacedItem).where(PlacedItem.plan_id == plan_id,
                                                    PlacedItem.equipment_id == p["equipment_id"]))
        if exists:
            continue
        db.add(PlacedItem(plan_id=plan_id, equipment_id=p["equipment_id"], kind="equipment",
                          label=p.get("label", ""), x=p["x"], y=p["y"], w=p["w"], h=p["h"],
                          rotation=p.get("rotation", 0), created_by=actor))
        n += 1
    db.flush()
    return n
