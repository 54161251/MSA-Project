"""Streamlit wrapper for the floor plan component (plain HTML/JS, no build step)."""

from __future__ import annotations

import base64
import io
from pathlib import Path

import streamlit.components.v1 as components

_component = components.declare_component(
    "msa_floorplan", path=str(Path(__file__).parent / "components" / "floorplan"))


def image_size(data_url: str | None) -> tuple[int, int]:
    if not data_url:
        return 1400, 1000
    from PIL import Image

    raw = base64.b64decode(data_url.split(",", 1)[1])
    with Image.open(io.BytesIO(raw)) as im:
        return im.size


def floorplan(plan_payload: dict, mode: str = "view", show_zones: bool = True, key: str = "fp"):
    plan = plan_payload["plan"]
    w, h = image_size(plan_payload.get("image"))
    findings = plan_payload.get("check", {}).get("data", {}).get("findings", [])
    flagged = sorted({i for f in findings for i in f.get("item_ids", [])})
    return _component(image=plan_payload.get("image"), image_w=w, image_h=h,
                      image_box=plan.get("image_box") or {}, width_m=plan["width_m"],
                      height_m=plan["height_m"], items=plan_payload["items"], mode=mode,
                      flagged=flagged, show_zones=show_zones, key=key, default=None)
