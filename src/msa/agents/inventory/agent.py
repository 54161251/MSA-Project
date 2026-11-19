"""Inventory agent.

"used two sheets of 3 mm acrylic"  -> InventoryTxn(delta=-2) on the matching item
"restocked 10 PLA spools"          -> InventoryTxn(delta=+10)
If the item is ambiguous or unknown, it asks which item instead of guessing.

Quantity on hand is the sum of the append-only transactions. When stock reaches the reorder
point an order is DRAFTED (A5): one draft per supplier, waiting for staff approval. The system
never places orders or spends money.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from msa.core.contracts import AgentResult
from msa.core.db import utcnow
from msa.core.llm import get_llm
from msa.core.models import InventoryItem, InventoryTxn, OrderDraft, User
from msa.core.registry import registry
from msa.core.services import ServiceError, notify, request_approval
from msa.core.textsearch import tokens

NUMBERS = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
           "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "fifteen": 15,
           "twenty": 20, "half": 0.5, "a couple": 2, "couple": 2, "a few": 3}
ADD_WORDS = ["restock", "restocked", "added", "add", "received", "delivered", "bought", "put back",
             "returned"]
USE_WORDS = ["used", "use", "took", "take", "consumed", "cut", "printed", "wasted", "finished"]


def on_hand(db: Session, item_id: str) -> float:
    return float(db.scalar(select(func.coalesce(func.sum(InventoryTxn.delta), 0.0))
                           .where(InventoryTxn.item_id == item_id)) or 0.0)


def _qty(text: str) -> float | None:
    t = text.lower()
    m = re.search(r"(?<![\d.])(\d+(?:\.\d+)?)(?!\s*(?:mm|cm|in\b|inch|ml|l\b|kg|g\b|%|x))", t)
    if m:
        return float(m.group(1))
    for w in sorted(NUMBERS, key=len, reverse=True):
        if re.search(rf"\b{w}\b", t):
            return float(NUMBERS[w])
    return None


def match_items(db: Session, text: str) -> list[tuple[float, InventoryItem]]:
    items = db.scalars(select(InventoryItem).where(InventoryItem.active.is_(True))).all()
    t = text.lower()
    q = set(tokens(text))
    scored = []
    for it in items:
        best = 0.0
        for alias in [it.name, *it.aliases]:
            a = alias.lower()
            if re.search(rf"(?<![a-z0-9]){re.escape(a)}(?![a-z0-9])", t):
                best = max(best, 1.0 + len(a) / 100)
            at = set(tokens(alias))
            if at:
                best = max(best, len(at & q) / len(at) * 0.9)
        if best >= 0.5:
            scored.append((best, it))
    scored.sort(key=lambda x: -x[0])
    return scored


def deterministic_parse(db: Session, text: str) -> dict[str, Any]:
    t = text.lower()
    direction = 1 if any(re.search(rf"\b{w}\b", t) for w in ADD_WORDS) else -1
    matches = match_items(db, text)
    qty = _qty(text)
    return {"direction": direction, "quantity": qty,
            "candidates": [{"item_id": it.id, "name": it.name, "score": round(s, 2)}
                           for s, it in matches[:4]]}


SYSTEM = (
    "Turn a makerspace member's note about using or restocking supplies into one stock change. "
    "Pick item_id only from the candidates. direction is -1 for used/taken and 1 for added. "
    'If unsure which item, set item_id to null. Return {"item_id", "quantity", "direction"}'
)


def _staff_only_restock(db: Session, actor: str) -> AgentResult | None:
    """Members log use; only staff add stock (A2). Checked here, on the server, for every caller."""
    user = db.get(User, actor)
    if user is not None and user.role == "staff":
        return None
    return AgentResult(agent="inventory", status="refused", output={
        "message": "Only staff can add stock. Please ask a staff member to restock or to record "
                   "the delivery."})


@registry.agent("inventory")
def log_use(db: Session, payload: dict, actor: str) -> AgentResult:
    text = (payload.get("text") or "").strip()
    if payload.get("item_id"):
        item = db.get(InventoryItem, payload["item_id"])
        if item is None:
            raise ServiceError("Unknown item")
        qty = float(payload.get("quantity") or 0)
        direction = 1 if int(payload.get("direction") or -1) > 0 else -1  # only the sign counts
        if direction > 0 and (refused := _staff_only_restock(db, actor)):
            return refused
    else:
        base = deterministic_parse(db, text)
        cands = base["candidates"]
        fallback = {"item_id": cands[0]["item_id"] if len(cands) == 1 or (
            len(cands) > 1 and cands[0]["score"] - cands[1]["score"] >= 0.3) else None,
            "quantity": base["quantity"], "direction": base["direction"]}
        out = get_llm().complete_json("inventory.parse", SYSTEM,
                                      f"Note: {text}\nCandidates: {cands}", lambda: fallback,
                                      max_tokens=150)
        ids = {c["item_id"] for c in cands}
        item_id = out.get("item_id") if out.get("item_id") in ids else None
        qty = out.get("quantity") if isinstance(out.get("quantity"), (int, float)) else base["quantity"]
        direction = 1 if out.get("direction") == 1 else -1
        if direction > 0 and (refused := _staff_only_restock(db, actor)):
            return refused
        if item_id is None or not qty:
            return AgentResult(agent="inventory", status="refused", output={
                "needs_clarification": True,
                "message": "Which item and how many?" if not qty else "Which item did you mean?",
                "options": cands, "quantity": qty, "direction": direction})
        item = db.get(InventoryItem, item_id)
    if qty <= 0 or qty > 10000:
        raise ServiceError("Quantity must be a positive number")
    txn = InventoryTxn(item_id=item.id, delta=direction * qty, user_id=actor, note="",
                       source_text=text, created_by=actor)
    db.add(txn)
    db.flush()
    level = on_hand(db, item.id)
    out = {"item_id": item.id, "item": item.name, "delta": txn.delta, "on_hand": level, "unit": item.unit}
    if level < 0:
        notify(db, "staff", f"inventory:negative:{item.id}",
               f"{item.name}: stock went below zero ({level:g}); please count it.", "warning")
    order = check_reorder(db, item)
    if order and order.status == "draft":
        out["order_draft_id"] = order.id
    return AgentResult(agent="inventory", status="ok", output=out,
                       trace=[f"parse -> {item.id} x{direction * qty:g}"])


def check_reorder(db: Session, item: InventoryItem) -> OrderDraft | None:
    level = on_hand(db, item.id)
    if level > item.reorder_point or item.reorder_qty <= 0:
        return None
    # Already on an order that is waiting for staff ("draft") or approved and on its way
    # ("approved", until it is marked received): don't order it a second time.
    open_orders = select(OrderDraft).where(OrderDraft.status.in_(["draft", "approved"]))
    for d in db.scalars(open_orders).all():
        if any(i["item_id"] == item.id for i in d.items):
            return d
    draft = db.scalar(select(OrderDraft).where(OrderDraft.status == "draft",
                                               OrderDraft.supplier == item.supplier))
    line = {"item_id": item.id, "name": item.name, "qty": item.reorder_qty, "unit": item.unit,
            "on_hand": level}
    if draft is None:
        draft = OrderDraft(supplier=item.supplier, items=[line], created_by="inventory")
        db.add(draft)
        db.flush()
    else:
        draft.items = [*draft.items, line]
    request_approval(db, "order_draft", draft.id, {"supplier": draft.supplier}, "inventory",
                     f"Order draft for {draft.supplier or 'supplier'}: "
                     + ", ".join(f"{i['qty']:g} {i['unit']} {i['name']}" for i in draft.items))
    return draft


def forecast(db: Session, item: InventoryItem, now: datetime | None = None, days: int = 30) -> dict:
    now = now or utcnow()
    since = now - timedelta(days=days)
    used = -float(db.scalar(select(func.coalesce(func.sum(InventoryTxn.delta), 0.0)).where(
        InventoryTxn.item_id == item.id, InventoryTxn.delta < 0, InventoryTxn.created_at >= since)) or 0)
    per_day = used / days
    level = on_hand(db, item.id)
    return {"on_hand": level, "used_last_30_days": used, "per_day": round(per_day, 3),
            "days_left": round(level / per_day, 1) if per_day > 0 else None}


def daily(db: Session) -> dict:
    drafted = []
    for item in db.scalars(select(InventoryItem).where(InventoryItem.active.is_(True))).all():
        f = forecast(db, item)
        if f["days_left"] is not None and f["days_left"] <= 7 and f["on_hand"] > item.reorder_point:
            notify(db, "staff", f"inventory:forecast:{item.id}",
                   f"{item.name} may run out in about {f['days_left']:g} days at the current rate.")
        d = check_reorder(db, item)
        if d and d.status == "draft":
            drafted.append(d.id)
    return {"order_drafts": sorted(set(drafted))}


registry.daily_job("inventory")(daily)
