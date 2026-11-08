"""Floor plan routes, the layout_change approval, and the floor plan seeder (R2)."""

from __future__ import annotations

import base64
import mimetypes
from pathlib import Path

import yaml
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from msa.agents.layout import agent
from msa.agents.layout.outline import draw_outline, image_box
from msa.core import orchestrator
from msa.core.auth import current_user, require_staff
from msa.core.config import DATA_DIR, VAR_DIR
from msa.core.db import get_db, new_id
from msa.core.models import Equipment, FloorPlan, PlacedItem, User, to_dict
from msa.core.registry import registry
from msa.core.services import audit, escalate
from msa.safety_tools.tools import check_layout

router = APIRouter(prefix="/floorplans", tags=["floor plan"])
UPLOADS = VAR_DIR / "floorplans"
SAFETY_TYPES = ("extinguisher", "first_aid", "eyewash", "estop", "exit")
SHAPES = ("rect", "circle", "oval", "triangle")
TO_M = {"m": 1.0, "cm": 0.01, "ft": 0.3048, "in": 0.0254}


class ItemIn(BaseModel):
    kind: str = "object"  # equipment | object | safety
    equipment_id: str | None = None
    shape: str = "rect"
    label: str = ""
    safety_type: str | None = None
    x: float = 0.5
    y: float = 0.5
    w: float | None = None
    h: float | None = None
    units: str = "m"
    rotation: float = 0


class MoveIn(BaseModel):
    id: str
    x: float
    y: float
    w: float | None = None
    h: float | None = None
    rotation: float | None = None


class SaveIn(BaseModel):
    items: list[MoveIn] = Field(default_factory=list)


def _image_path(plan: FloorPlan) -> Path | None:
    if not plan.image_path:
        return None
    p = Path(plan.image_path)
    if not p.is_absolute():
        p = (DATA_DIR / p) if (DATA_DIR / p).exists() else (UPLOADS / p)
    return p if p.exists() else None


def plan_payload(db: Session, plan: FloorPlan, with_image: bool = True) -> dict:
    items = db.scalars(select(PlacedItem).where(PlacedItem.plan_id == plan.id)).all()
    eqs = {e.id: e for e in db.scalars(select(Equipment)).all()}
    out_items = []
    for it in items:
        d = to_dict(it)
        eq = eqs.get(it.equipment_id or "")
        if eq:
            from msa.safety_tools.tools import _clearance_rect

            z = _clearance_rect(it, eq)
            d.update({"status": eq.status, "status_reason": eq.status_reason,
                      "category": eq.category, "name": eq.name,
                      "zone": {"x0": z[0], "y0": z[1], "x1": z[2], "y1": z[3]}})
        out_items.append(d)
    out = {"plan": to_dict(plan), "items": out_items,
           "check": check_layout(db, plan.id).model_dump(),
           "unplaced": [e.id for e in eqs.values() if e.status != "retired"
                        and e.id not in {i.equipment_id for i in items}]}
    img = _image_path(plan)
    if with_image and img:
        mime = mimetypes.guess_type(img.name)[0] or "image/png"
        out["image"] = f"data:{mime};base64," + base64.b64encode(img.read_bytes()).decode()
    return out


@router.get("")
def list_plans(user: User = Depends(current_user), db: Session = Depends(get_db)):
    return [to_dict(p) for p in db.scalars(select(FloorPlan).order_by(FloorPlan.created_at)).all()]


@router.get("/{plan_id}")
def get_plan(plan_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    plan = db.get(FloorPlan, plan_id)
    if plan is None:
        raise HTTPException(404, "No such floor plan")
    return plan_payload(db, plan)


@router.post("")
async def create_plan(name: str = Form("Main floor"), width: float = Form(...), height: float = Form(...),
                      units: str = Form("m"), file: UploadFile | None = File(None),
                      staff: User = Depends(require_staff), db: Session = Depends(get_db)):
    """Admin uploads the room drawing (image or PDF first page) with the room's real size."""
    f = TO_M.get(units, 1.0)
    plan = FloorPlan(name=name, width_m=width * f, height_m=height * f, created_by=staff.id)
    if file is not None and file.filename:
        UPLOADS.mkdir(parents=True, exist_ok=True)
        data = await file.read()
        fname = f"{new_id()[:10]}-{Path(file.filename).stem}"
        if file.filename.lower().endswith(".pdf"):
            data, fname = _pdf_first_page_png(data), fname + ".png"
        else:
            fname += Path(file.filename).suffix.lower()
        (UPLOADS / fname).write_bytes(data)
        plan.image_path = fname
    else:
        fname = f"{new_id()[:10]}-outline.png"
        draw_outline(plan.width_m, plan.height_m, [], UPLOADS / fname)
        plan.image_path = fname
        b = image_box(plan.width_m, plan.height_m)
        plan.image_box = {"x0": b["margin_px"], "y0": b["margin_px"],
                          "x1": b["width_px"] - b["margin_px"], "y1": b["height_px"] - b["margin_px"]}
    db.add(plan)
    db.flush()
    audit(db, staff.id, "floorplan.created", "floorplan", plan.id, None, {"name": name})
    return to_dict(plan)


def _pdf_first_page_png(data: bytes) -> bytes:
    """Best effort: the first embedded image of the PDF's first page (vector PDFs: export a
    PNG from your drawing tool instead)."""
    import io

    from pypdf import PdfReader

    page = PdfReader(io.BytesIO(data)).pages[0]
    for img in page.images:
        return img.data
    raise HTTPException(400, "Couldn't read an image from that PDF; please export the plan as PNG")


@router.post("/{plan_id}/items")
def add_item(plan_id: str, body: ItemIn, staff: User = Depends(require_staff), db: Session = Depends(get_db)):
    plan = db.get(FloorPlan, plan_id)
    if plan is None:
        raise HTTPException(404, "No such floor plan")
    f = TO_M.get(body.units, 1.0)
    if body.kind == "equipment":
        eq = db.get(Equipment, body.equipment_id or "")
        if eq is None:
            raise HTTPException(400, "Pick a registered machine")
        w, h, label = eq.footprint_w, eq.footprint_d, eq.name
    else:
        if body.shape not in SHAPES:
            raise HTTPException(400, f"shape must be one of {SHAPES}")
        if body.kind == "safety" and body.safety_type not in SAFETY_TYPES:
            raise HTTPException(400, f"safety_type must be one of {SAFETY_TYPES}")
        w, h, label = (body.w or 1) * f, (body.h or body.w or 1) * f, body.label
    it = PlacedItem(plan_id=plan.id, kind=body.kind, equipment_id=body.equipment_id, shape=body.shape,
                    label=label, safety_type=body.safety_type, x=body.x * f, y=body.y * f, w=w, h=h,
                    rotation=body.rotation, created_by=staff.id)
    db.add(it)
    db.flush()
    audit(db, staff.id, "floorplan.item_added", "floorplan", plan.id, None, to_dict(it))
    return plan_payload(db, plan, with_image=False)


@router.post("/{plan_id}/save")
def save_positions(plan_id: str, body: SaveIn, staff: User = Depends(require_staff),
                   db: Session = Depends(get_db)):
    plan = db.get(FloorPlan, plan_id)
    if plan is None:
        raise HTTPException(404, "No such floor plan")
    moved = 0
    for m in body.items:
        it = db.get(PlacedItem, m.id)
        if it is None or it.plan_id != plan.id:
            continue
        before = {"x": it.x, "y": it.y, "rotation": it.rotation}
        it.x, it.y = round(m.x, 3), round(m.y, 3)
        if m.rotation is not None and m.rotation != it.rotation:
            if int(round((m.rotation - it.rotation) / 90)) % 2:
                it.w, it.h = it.h, it.w
            it.rotation = m.rotation % 360
        moved += 1
        audit(db, staff.id, "floorplan.item_moved", "placed_item", it.id, before,
              {"x": it.x, "y": it.y, "rotation": it.rotation})
    db.flush()
    payload = plan_payload(db, plan, with_image=False)
    findings = payload["check"]["data"].get("findings", [])
    if findings:
        escalate(db, "E11", "floorplan", plan.id, "; ".join(f["text"] for f in findings)[:600], staff.id)
    return {**payload, "moved": moved}


@router.delete("/{plan_id}/items/{item_id}")
def delete_item(plan_id: str, item_id: str, staff: User = Depends(require_staff),
                db: Session = Depends(get_db)):
    it = db.get(PlacedItem, item_id)
    if it is None or it.plan_id != plan_id:
        raise HTTPException(404, "No such item")
    audit(db, staff.id, "floorplan.item_removed", "placed_item", it.id, to_dict(it), None)
    db.delete(it)
    db.flush()
    return {"deleted": item_id}


@router.post("/{plan_id}/suggest")
def suggest(plan_id: str, staff: User = Depends(require_staff), db: Session = Depends(get_db)):
    final, _ = orchestrator.run(db, "layout", {"plan_id": plan_id}, staff.id)
    return final.model_dump()


registry.router(router)


@registry.on_approve("layout_change")
def _apply(db, approval, staff) -> None:
    agent.apply_proposals(db, approval.target_id, approval.payload.get("proposals", []), staff.id)


@registry.seeder(40, "floor plan")
def seed(db: Session, approve_all: bool = False) -> None:
    p = DATA_DIR / "floorplan.yaml"
    if not p.exists() or db.scalar(select(FloorPlan)):
        return
    cfg = yaml.safe_load(p.read_text(encoding="utf-8"))
    img = DATA_DIR / cfg.get("image", "floorplan.png")
    if not img.exists():
        draw_outline(cfg["width_m"], cfg["height_m"], cfg.get("exits", []), img)
    b = image_box(cfg["width_m"], cfg["height_m"])
    plan = FloorPlan(name=cfg["name"], image_path=img.name, width_m=cfg["width_m"],
                     height_m=cfg["height_m"], exits=[{"x": e["x"], "y": e["y"]} for e in cfg["exits"]],
                     image_box={"x0": b["margin_px"], "y0": b["margin_px"],
                                "x1": b["width_px"] - b["margin_px"], "y1": b["height_px"] - b["margin_px"]},
                     created_by="seed")
    db.add(plan)
    db.flush()
    for it in cfg.get("items", []):
        eq = db.get(Equipment, it["equipment_id"]) if it.get("equipment_id") else None
        if it.get("equipment_id") and eq is None:
            continue
        db.add(PlacedItem(
            plan_id=plan.id, equipment_id=it.get("equipment_id"),
            kind="equipment" if eq else it.get("kind", "object"), shape=it.get("shape", "rect"),
            label=eq.name if eq else it.get("label", ""), safety_type=it.get("safety_type"),
            x=it["x"], y=it["y"], w=eq.footprint_w if eq else it["w"], h=eq.footprint_d if eq else it["h"],
            rotation=it.get("rotation", 0), created_by="seed"))
    db.flush()
