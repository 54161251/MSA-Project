"""Spec extraction with a confidence per field. Low confidence on a field the system needs
(the footprint, used by the floor plan) raises E12 so staff fill it in."""

from __future__ import annotations

import re
from typing import Any

from msa.core.docs import sections
from msa.core.llm import get_llm
from msa.core.models import Document

REQUIRED = ["footprint"]
CONFIDENCE_MIN = 0.7

DIM = re.compile(r"(\d+(?:\.\d+)?)\s*(mm|cm|m|in|ft)?\s*[x×]\s*(\d+(?:\.\d+)?)\s*(mm|cm|m|in|ft)", re.I)
TO_M = {"mm": 0.001, "cm": 0.01, "m": 1.0, "in": 0.0254, "ft": 0.3048}


def parse_dims(value: str) -> tuple[float, float] | None:
    m = DIM.search(value)
    if not m:
        return None
    u1 = (m.group(2) or m.group(4)).lower()
    u2 = m.group(4).lower()
    return round(float(m.group(1)) * TO_M[u1], 3), round(float(m.group(3)) * TO_M[u2], 3)


def deterministic_specs(manual: Document) -> dict[str, Any]:
    specs: dict[str, Any] = {}
    conf: dict[str, float] = {}
    loc: dict[str, str] = {}
    for sec in sections(manual):
        if not sec["heading"].lower().startswith(("spec", "technical")):
            continue
        for line in sec["text"].splitlines():
            m = re.match(r"^\s*[-*]?\s*([A-Za-z][A-Za-z /()-]{1,40}):\s*(.+)$", line)
            if m:
                key = m.group(1).strip().lower().replace(" ", "_")
                specs[key] = m.group(2).strip()
                conf[key] = 0.95
                loc[key] = sec["locator"]
    fp = None
    if "footprint" in specs:
        fp = parse_dims(specs["footprint"])
        if fp is None:
            conf["footprint"] = 0.3
    else:
        conf["footprint"] = 0.0
    return {"specs": specs, "footprint_m": list(fp) if fp else None, "confidence": conf,
            "locators": loc}


SYSTEM = (
    "Extract machine specifications from the manual text. Copy values exactly. For each field "
    "give a confidence from 0 to 1. Give footprint_m as [width, depth] in metres, or null if the "
    'manual does not state it. Return {"specs": {}, "footprint_m": null, "confidence": {}}'
)


def extract_specs(manual: Document) -> dict[str, Any]:
    fallback = deterministic_specs(manual)
    out = get_llm().complete_json("intake.extract_specs", SYSTEM, manual.body[:12000],
                                  lambda: fallback)
    if not isinstance(out.get("specs"), dict):
        out["specs"] = {}
    if not isinstance(out.get("confidence"), dict):
        out["confidence"] = {}
    out.setdefault("locators", fallback.get("locators", {}))
    fp = out.get("footprint_m")
    try:
        fp = [float(fp[0]), float(fp[1])] if isinstance(fp, (list, tuple)) and len(fp) == 2 else None
    except (TypeError, ValueError):
        fp = None
    out["footprint_m"] = fp
    # A value the deterministic parser read exactly from the spec table always wins: the model
    # may add fields, but it can't make a clearly stated footprint "uncertain" (or change it).
    out["specs"] = {**out["specs"], **fallback["specs"]}
    for k, v in fallback["confidence"].items():
        if v >= CONFIDENCE_MIN:
            out["confidence"][k] = v
    if fallback.get("footprint_m"):
        out["footprint_m"] = fallback["footprint_m"]
        out["confidence"]["footprint"] = max(out["confidence"].get("footprint", 0), 0.95)
    elif fp:
        out["confidence"].setdefault("footprint", 0.8)
    return out


def low_confidence(extracted: dict[str, Any]) -> list[str]:
    conf = extracted.get("confidence", {})
    missing = [f for f in REQUIRED if conf.get(f, 0.0) < CONFIDENCE_MIN]
    if "footprint" in REQUIRED and not extracted.get("footprint_m") and "footprint" not in missing:
        missing.append("footprint")
    return missing
