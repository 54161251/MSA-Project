"""Turn manual sections into structured, cited statements (SOP, training, materials).

The deterministic path maps manual headings to SOP sections and keeps every bullet with a
citation to the exact manual page and section. The LLM path (when enabled) may rephrase and
merge, but must cite locators from the list it is given; citations it invents are dropped,
so Review flags those statements (E07) instead of trusting them.
"""

from __future__ import annotations

import re
from typing import Any

from msa.core.contracts import Citation, Statement
from msa.core.docs import citation_for, sections
from msa.core.llm import get_llm
from msa.core.models import Document, Equipment

SOP_SECTIONS = ["PPE", "Warnings", "Before you start", "Operation", "Material rules", "Emergency"]

# manual heading (lowercase, startswith) -> (SOP section, statement kind)
HEADING_MAP: list[tuple[str, str, str]] = [
    ("personal protective", "PPE", "ppe"),
    ("ppe", "PPE", "ppe"),
    ("safety", "Warnings", "warning"),
    ("warning", "Warnings", "warning"),
    ("before you start", "Before you start", "step"),
    ("setup", "Before you start", "step"),
    ("set up", "Before you start", "step"),
    ("operation", "Operation", "step"),
    ("operating", "Operation", "step"),
    ("using", "Operation", "step"),
    ("material", "Material rules", "material"),
    ("emergenc", "Emergency", "emergency"),
]

SAFETY_KINDS = {"ppe", "step", "warning", "material", "emergency"}


def map_heading(heading: str) -> tuple[str, str] | None:
    h = heading.lower().strip()
    for prefix, section, kind in HEADING_MAP:
        if h.startswith(prefix):
            return section, kind
    return None


def bullets(text: str) -> list[str]:
    items, cur = [], []
    for line in text.splitlines():
        s = line.strip()
        if s.startswith(("- ", "* ")):
            if cur:
                items.append(" ".join(cur))
            cur = [s[2:].strip()]
        elif s and cur:
            cur.append(s)
        elif s:
            items.append(s)
    if cur:
        items.append(" ".join(cur))
    return [i for i in items if len(i) > 2]


def deterministic_statements(sources: list[Document]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for doc in sources:
        for sec in sections(doc):
            mapped = map_heading(sec["heading"])
            if mapped is None:
                continue
            section, kind = mapped
            for b in bullets(sec["text"]):
                c = citation_for(doc, sec, b)
                out.append(Statement(section=section, kind=kind, text=b,
                                     citations=[c]).model_dump())
    order = {s: i for i, s in enumerate(SOP_SECTIONS)}
    out.sort(key=lambda s: order.get(s["section"], 99))
    return out


SYSTEM_SOP = (
    "You draft a makerspace Standard Operating Procedure from manual excerpts. Write short, "
    "plain sentences a beginner can follow. Use only facts in the excerpts. Every statement must "
    "cite at least one excerpt by its exact document_id, version and locator, with a short quote "
    "copied from that excerpt. Sections: " + ", ".join(SOP_SECTIONS) + ". Statement kinds: ppe, "
    'warning, step, material, emergency, info. Return {"statements": [{"section", "kind", "text", '
    '"citations": [{"document_id", "version", "locator", "quote"}]}]}'
)


def draft_sop_statements(eq: Equipment, sources: list[Document]) -> list[dict[str, Any]]:
    excerpts = []
    for d in sources:
        for s in sections(d):
            excerpts.append(f"[{d.id} v{d.version} | {s['locator']}]\n{s['text']}")
    prompt = f"Machine: {eq.name} ({eq.category})\n\nExcerpts:\n\n" + "\n\n".join(excerpts)
    out = get_llm().complete_json("intake.draft_sop", SYSTEM_SOP, prompt,
                                  lambda: {"statements": deterministic_statements(sources)},
                                  max_tokens=8000)
    secs = [(d, sec) for d in sources for sec in sections(d)]
    raw = out.get("statements") or out.get("sop") or []
    if isinstance(raw, dict):  # {"PPE": [...], "Operation": [...]} style
        raw = [{**(x if isinstance(x, dict) else {"text": str(x)}), "section": k}
               for k, v in raw.items() for x in (v if isinstance(v, list) else [v])]
    clean = []
    for st in raw:
        s = coerce_statement(st)
        if s is None:
            continue
        fixed, seen = [], set()
        for c in s.citations:
            hit = resolve_citation(c, s.text, secs)
            if hit is not None and (hit.document_id, hit.locator) not in seen:
                seen.add((hit.document_id, hit.locator))
                fixed.append(hit)
        s.citations = fixed  # unresolvable citations are dropped, so Review flags the line (E07)
        clean.append(s.model_dump())
    return complete_with_manual(clean, deterministic_statements(sources))


def complete_with_manual(drafted: list[dict[str, Any]], manual: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Safety rules don't depend on the model's wording:
    * the Material rules section is always the manual's own lines (they become the machine's
      material rules, so they must be exact);
    * any required section the model left out is filled with the manual's cited lines."""
    out = [s for s in drafted if s["section"] != "Material rules"]
    out += [m for m in manual if m["section"] == "Material rules"]
    present = {s["section"] for s in out}
    for m in manual:
        if m["section"] not in present:
            out.append(m)
    order = {sec: i for i, sec in enumerate(SOP_SECTIONS)}
    out.sort(key=lambda s: order.get(s["section"], 99))
    return out


def _version(v: Any) -> int:
    """Models write versions as 1, "1" or "v1"."""
    m = re.search(r"\d+", str(v if v is not None else 1))
    return int(m.group()) if m else 1


KIND_ALIASES = {
    "ppe": "ppe", "personal_protective_equipment": "ppe", "protective_equipment": "ppe",
    "warning": "warning", "hazard": "warning", "caution": "warning", "danger": "warning",
    "safety": "warning", "step": "step", "procedure": "step", "instruction": "step",
    "requirement": "step", "check": "step", "operation": "step", "setup": "step",
    "material": "material", "materials": "material", "material_rule": "material",
    "emergency": "emergency", "emergency_procedure": "emergency", "info": "info", "note": "info",
}


def coerce_statement(st: Any) -> Statement | None:
    """Accept the shapes models actually return (different key names, kinds, citation forms)."""
    if isinstance(st, str):
        st = {"text": st}
    if not isinstance(st, dict):
        return None
    text = str(st.get("text") or st.get("statement") or st.get("content") or "").strip()
    if not text:
        return None
    section_in = str(st.get("section") or st.get("heading") or "")
    kind = str(st.get("kind") or st.get("type") or "").strip().lower().replace(" ", "_")
    kind = KIND_ALIASES.get(kind, "")
    section = normalize_section(section_in, kind or "step")
    if not kind or (kind == "info" and section != "Operation"):
        kind = {v: k for k, v in KIND_SECTION.items()}.get(section, "step")
    cites = st.get("citations") or st.get("sources") or st.get("citation") or []
    if not isinstance(cites, list):
        cites = [cites]
    parsed = []
    for c in cites:
        if isinstance(c, str):
            m = re.match(r"\[?\s*([0-9a-f]{8,})\s*(?:v(\d+))?\s*\|?\s*([^\]]*)\]?", c.strip())
            c = {"document_id": m.group(1), "version": m.group(2) or 1, "locator": m.group(3)} if m \
                else {"document_id": "", "locator": c}
        if not isinstance(c, dict):
            continue
        try:
            parsed.append(Citation(
                document_id=str(c.get("document_id") or c.get("doc_id") or c.get("document") or ""),
                version=_version(c.get("version")),
                locator=str(c.get("locator") or c.get("location") or c.get("section") or c.get("page") or ""),
                quote=str(c.get("quote") or c.get("excerpt") or "")))
        except (ValueError, TypeError):
            continue
    return Statement(section=section, kind=kind, text=text, citations=parsed)


KIND_SECTION = {"ppe": "PPE", "warning": "Warnings", "step": "Operation",
                "material": "Material rules", "emergency": "Emergency"}


def normalize_section(name: str, kind: str) -> str:
    """Map whatever section name the model used onto the fixed SOP sections."""
    low = (name or "").strip().lower()
    for sec in SOP_SECTIONS:
        if low == sec.lower():
            return sec
    mapped = map_heading(low) if low else None
    if mapped:
        return mapped[0]
    return KIND_SECTION.get(kind, "Operation")


def _best_sentence(text: str, passage: str) -> str:
    from msa.core.textsearch import tokens

    sents = [x.strip(" -*") for x in re.split(r"(?<=[.!?;])\s+|\n+", passage) if x.strip(" -*")]
    if not sents:
        return passage[:200]
    want = set(tokens(text))
    return max(sents, key=lambda x: len(want & set(tokens(x))) / (len(set(tokens(x))) or 1))


def resolve_citation(c: Citation, text: str, secs: list) -> Citation | None:
    """Point a model-written citation at a real section, and take the quote from the source.

    Models often get the pointer roughly right but not exactly ("p.2 PPE" for
    "p.2 Personal protective equipment", or a paraphrased quote). The pointer is matched to a
    real section; the quote is replaced by the source sentence closest to the statement, so
    what Review checks is always text that really is in the manual."""
    loc = (c.locator or "").lower().strip()
    cands = [(d, sec) for d, sec in secs if d.id == c.document_id] or secs
    exact = [(d, sec) for d, sec in cands if sec["locator"].lower() == loc]
    if not exact:
        page = re.search(r"p\.?\s*(\d+)", loc)
        heading = re.sub(r"^p\.?\s*\d+\s*", "", loc).strip()
        exact = [(d, sec) for d, sec in cands
                 if heading and (heading in sec["heading"].lower() or sec["heading"].lower() in heading)
                 and (not page or sec["page"] == int(page.group(1)))]
    if not exact and c.quote:
        q = " ".join(c.quote.lower().split())
        exact = [(d, sec) for d, sec in cands if q[:40] in " ".join(sec["text"].lower().split())]
    if not exact:
        return None
    d, sec = exact[0]
    return Citation(document_id=d.id, version=d.version, locator=sec["locator"],
                    quote=" ".join(_best_sentence(text, sec["text"]).split()[:40]))


def training_statements(eq: Equipment, sop: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Training doc: same cited facts, ordered for learning."""
    by = {k: [s for s in sop if s["section"] == k] for k in SOP_SECTIONS}
    out = [Statement(section="What this machine is", kind="info",
                     text=f"The {eq.name} is a {eq.category.replace('_', ' ')}. Read this guide, "
                     "then take the quiz.").model_dump()]
    for title, key in [("Key hazards", "Warnings"), ("What to wear and do", "PPE"),
                       ("Materials", "Material rules"), ("Before you start", "Before you start"),
                       ("Step by step", "Operation"), ("In an emergency", "Emergency")]:
        for s in by.get(key, []):
            out.append({**s, "section": title})
    for link in eq.video_links or []:
        out.append(Statement(section="Videos", kind="info", text=f"Training video: {link}")
                   .model_dump())
    return out


MATERIAL_PREFIX = re.compile(r"^\s*(allowed|approved|ok|never|banned|not allowed|ask staff|ask)\s*[:\-]",
                             re.I)


def material_verdict(text: str) -> str | None:
    m = MATERIAL_PREFIX.match(text)
    if m:
        word = m.group(1).lower()
        if word in ("allowed", "approved", "ok"):
            return "allowed"
        if word in ("never", "banned", "not allowed"):
            return "banned"
        return "ask_staff"
    low = text.lower()
    if re.search(r"\b(never|not allowed|do not|don't|must not|prohibited|banned|forbidden)\b", low):
        return "banned"
    if re.search(r"\b(ask staff|check with staff|staff approval|only with staff)\b", low):
        return "ask_staff"
    if re.search(r"\b(allowed|approved|ok to|okay to|can be (cut|used|engraved|printed))\b", low):
        return "allowed"
    return None


def material_clause(text: str) -> str:
    """'Never: PVC or vinyl. It releases chlorine gas.' -> 'Never: PVC or vinyl' (no reasons)."""
    return re.split(r"\.(?:\s|$)", text, maxsplit=1)[0]


def render_markdown(title: str, statements: list[dict[str, Any]]) -> str:
    lines = [f"# {title}", ""]
    current = None
    for s in statements:
        if s["section"] != current:
            current = s["section"]
            lines += ["", f"## {current}"]
        refs = "; ".join(c["locator"] for c in s.get("citations", []))
        lines.append(f"- {s['text']}" + (f"  *(source: {refs})*" if refs else ""))
    return "\n".join(lines).strip() + "\n"


def citations_of(statement: dict[str, Any]) -> list[Citation]:
    return [Citation(**c) for c in statement.get("citations", [])]
