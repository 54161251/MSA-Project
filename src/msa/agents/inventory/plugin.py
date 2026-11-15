"""Registers Inventory: routes (log use, items, export), order approvals, seeder."""

from __future__ import annotations

import csv
import io

import yaml
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from msa.agents.inventory import agent
from msa.core import orchestrator
from msa.core.auth import current_user, require_staff
from msa.core.config import DATA_DIR
from msa.core.db import get_db
from msa.core.models import InventoryItem, InventoryTxn, OrderDraft, User, to_dict
from msa.core.registry import registry
from msa.core.services import audit

router = APIRouter(prefix="/inventory", tags=["inventory"])


class UseIn(BaseModel):
    text: str = ""
    item_id: str | None = None
    quantity: float | None = None
    direction: int = -1


class ItemIn(BaseModel):
    name: str = Field(min_length=1)
    unit: str = "each"
    category: str = "material"
    aliases: list[str] = []
    reorder_point: float = 0
    reorder_qty: float = 0
    supplier: str = ""
    unit_cost: float | None = None
    starting_qty: float = 0


class ItemPatch(BaseModel):
    name: str | None = None
    unit: str | None = None
    category: str | None = None
    aliases: list[str] | None = None
    reorder_point: float | None = None
    reorder_qty: float | None = None
    supplier: str | None = None
    unit_cost: float | None = None
    active: bool | None = None


class CountIn(BaseModel):
    counted: float
    note: str = "stock count"


def item_row(db: Session, it: InventoryItem) -> dict:
    return {**to_dict(it), **agent.forecast(db, it)}


def _unique_name(db: Session, name: str) -> None:
    if not name.strip():
        raise HTTPException(400, "The item needs a name")
    if db.scalar(select(InventoryItem).where(InventoryItem.name == name)) is not None:
        raise HTTPException(400, f"An item called {name!r} already exists")


@router.get("")
def items(user: User = Depends(current_user), db: Session = Depends(get_db)):
    q = select(InventoryItem).where(InventoryItem.active.is_(True)).order_by(InventoryItem.category,
                                                                             InventoryItem.name)
    return [item_row(db, it) for it in db.scalars(q).all()]


@router.post("/use")
def use(body: UseIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    final, _ = orchestrator.run(db, "inventory", body.model_dump(), user.id)
    return final.model_dump()


@router.post("/items")
def add_item(body: ItemIn, staff: User = Depends(require_staff), db: Session = Depends(get_db)):
    data = body.model_dump()
    _unique_name(db, data["name"])
    start = data.pop("starting_qty")
    it = InventoryItem(created_by=staff.id, **data)
    db.add(it)
    db.flush()
    if start:
        db.add(InventoryTxn(item_id=it.id, delta=start, user_id=staff.id, note="starting stock",
                            created_by=staff.id))
    audit(db, staff.id, "inventory.item_added", "inventory_item", it.id, None, data)
    return item_row(db, it)


@router.patch("/items/{item_id}")
def edit_item(item_id: str, body: ItemPatch, staff: User = Depends(require_staff),
              db: Session = Depends(get_db)):
    it = db.get(InventoryItem, item_id)
    if it is None:
        raise HTTPException(404, "No such item")
    changes = body.model_dump(exclude_none=True)
    if "name" in changes and changes["name"] != it.name:
        _unique_name(db, changes["name"])
    for k, v in changes.items():
        setattr(it, k, v)
    audit(db, staff.id, "inventory.item_edit", "inventory_item", it.id, None, changes)
    return item_row(db, it)


@router.post("/items/{item_id}/count")
def count(item_id: str, body: CountIn, staff: User = Depends(require_staff),
          db: Session = Depends(get_db)):
    """A physical count adds a correcting transaction (history is never rewritten)."""
    it = db.get(InventoryItem, item_id)
    if it is None:
        raise HTTPException(404, "No such item")
    diff = body.counted - agent.on_hand(db, it.id)
    if diff:
        db.add(InventoryTxn(item_id=it.id, delta=diff, user_id=staff.id, note=body.note,
                            created_by=staff.id))
        db.flush()
    return item_row(db, it)


@router.get("/items/{item_id}/history")
def history(item_id: str, staff: User = Depends(require_staff), db: Session = Depends(get_db)):
    q = select(InventoryTxn).where(InventoryTxn.item_id == item_id).order_by(InventoryTxn.created_at.desc())
    return [to_dict(t) for t in db.scalars(q).all()]


@router.get("/orders")
def orders(status: str = "draft", staff: User = Depends(require_staff), db: Session = Depends(get_db)):
    q = select(OrderDraft).order_by(OrderDraft.created_at.desc())
    if status != "all":
        q = q.where(OrderDraft.status == status)
    return [to_dict(o) for o in db.scalars(q).all()]


@router.post("/orders/{order_id}/received")
def received(order_id: str, staff: User = Depends(require_staff), db: Session = Depends(get_db)):
    o = db.get(OrderDraft, order_id)
    if o is None or o.status != "approved":
        raise HTTPException(400, "Only an approved order can be marked received")
    for line in o.items:
        db.add(InventoryTxn(item_id=line["item_id"], delta=line["qty"], user_id=staff.id,
                            note=f"order {o.id[:8]} received", created_by=staff.id))
    o.status = "received"
    return to_dict(o)


@router.get("/export")
def export(fmt: str = "csv", staff: User = Depends(require_staff), db: Session = Depends(get_db)):
    rows = [item_row(db, it) for it in db.scalars(select(InventoryItem).order_by(InventoryItem.name)).all()]
    cols = ["name", "category", "unit", "on_hand", "reorder_point", "reorder_qty", "supplier",
            "used_last_30_days", "days_left"]
    if fmt == "xlsx":
        from openpyxl import Workbook

        wb = Workbook()
        ws = wb.active
        ws.title = "Inventory"
        ws.append(cols)
        for r in rows:
            ws.append([r.get(c) for c in cols])
        buf = io.BytesIO()
        wb.save(buf)
        buf.seek(0)
        return StreamingResponse(buf, media_type="application/vnd.openxmlformats-officedocument."
                                 "spreadsheetml.sheet",
                                 headers={"Content-Disposition": "attachment; filename=inventory.xlsx"})
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(cols)
    for r in rows:
        w.writerow([r.get(c) for c in cols])
    return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv",
                             headers={"Content-Disposition": "attachment; filename=inventory.csv"})


registry.router(router)


@registry.on_approve("order_draft")
def _approve(db, approval, staff) -> None:
    o = db.get(OrderDraft, approval.target_id)
    if o:
        o.status = "approved"  # staff place the order outside the system (A5)


@registry.on_reject("order_draft")
def _reject(db, approval, staff) -> None:
    o = db.get(OrderDraft, approval.target_id)
    if o:
        o.status = "rejected"
        o.reason = approval.reason


@registry.seeder(30, "inventory")
def seed(db: Session, approve_all: bool = False) -> None:
    p = DATA_DIR / "inventory.yaml"
    if not p.exists():
        return
    for row in yaml.safe_load(p.read_text(encoding="utf-8")) or []:
        if db.scalar(select(InventoryItem).where(InventoryItem.name == row["name"])):
            continue
        qty = row.pop("qty", 0)
        it = InventoryItem(created_by="seed", **row)
        db.add(it)
        db.flush()
        db.add(InventoryTxn(item_id=it.id, delta=qty, note="starting stock", created_by="seed"))
    db.flush()
