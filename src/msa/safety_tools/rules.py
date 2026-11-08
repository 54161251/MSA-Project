"""Load the YAML rule tables and match text against them."""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

RULES_DIR = Path(__file__).parent / "rules"


@lru_cache(maxsize=1)
def rules() -> dict[str, Any]:
    data: dict[str, Any] = {}
    for f in sorted(RULES_DIR.glob("*.yaml")):
        part = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
        for k, v in part.items():
            if isinstance(v, dict) and isinstance(data.get(k), dict):
                data[k].update(v)
            elif isinstance(v, list) and isinstance(data.get(k), list):
                data[k].extend(v)
            else:
                data[k] = v
    return data


def _has_phrase(text: str, phrase: str) -> bool:
    return re.search(r"(?<![a-z0-9])" + re.escape(str(phrase).lower()) + r"(?![a-z0-9])", text) is not None


def canonical_material(name: str) -> str | None:
    """'Plexiglass' -> 'acrylic'. None when the material is unknown."""
    t = " ".join(name.lower().replace("_", " ").split())
    mats = rules()["materials"]
    if t.replace(" ", "_") in mats:
        return t.replace(" ", "_")
    best: tuple[int, str] | None = None
    for canon, syns in mats.items():
        for s in syns:
            if t == s or _has_phrase(t, s):
                if best is None or len(s) > best[0]:
                    best = (len(s), canon)
    return best[1] if best else None


def find_materials(text: str) -> list[str]:
    """Every known material mentioned in free text, longest synonym wins."""
    t = text.lower()
    found: dict[str, int] = {}
    spans: list[tuple[int, int]] = []
    pairs = sorted(
        ((s, c) for c, syns in rules()["materials"].items() for s in syns),
        key=lambda p: -len(p[0]),
    )
    for syn, canon in pairs:
        for m in re.finditer(r"(?<![a-z0-9])" + re.escape(syn) + r"(?![a-z0-9])", t):
            if any(a <= m.start() < b for a, b in spans):
                continue
            spans.append((m.start(), m.end()))
            found.setdefault(canon, m.start())
    return sorted(found, key=found.get)


def find_categories(text: str) -> list[str]:
    t = text.lower()
    hits: dict[str, int] = {}
    claimed: list[tuple[int, int]] = []
    pairs = sorted(
        ((kw, c) for c, meta in rules()["categories"].items() for kw in meta["keywords"]),
        key=lambda p: -len(p[0]),
    )
    for kw, cat in pairs:
        pat = r"(?<![a-z0-9])" + re.escape(str(kw)) + r"(?:s|es|er|ers|ed|ing)?(?![a-z0-9])"
        for m in re.finditer(pat, t):
            if any(a <= m.start() < b for a, b in claimed):
                continue
            claimed.append((m.start(), m.end()))
            hits.setdefault(cat, m.start())
    return sorted(hits, key=hits.get)


def find_processes(text: str) -> list[str]:
    t = text.lower()
    return [p for p, kws in rules()["processes"].items() if any(_has_phrase(t, k) for k in kws)]


def parent_material(canon: str) -> str | None:
    """hardwood -> wood: a machine that allows "wood" allows its kinds."""
    return rules().get("material_parents", {}).get(canon)


def category_label(cat: str) -> str:
    return rules()["categories"].get(cat, {}).get("label", cat.replace("_", " "))
