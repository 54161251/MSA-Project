"""Thickness parsing for material rules ("Allowed: plywood up to 6 mm").

A material can be allowed *with a limit*, and a member's plan can exceed it. The limit lives in
words inside the rule's reason ("Allowed: plywood and MDF up to 6 mm."), so it is read here by a
deterministic parser rather than by the LLM (G2).

Two callers use this: the Project Check agent (to turn a too-thick plan into a staff escalation)
and the Ask agent (to stop answering "Yes" to a question the manual caps). Keeping one
implementation is the point: they disagreed when this logic lived in the agent.
"""

from __future__ import annotations

import re

_MM_PER = {"mm": 1.0, "millimetre": 1.0, "millimeter": 1.0, "cm": 10.0, "in": 25.4, "inch": 25.4,
           "inches": 25.4, '"': 25.4, "thou": 0.0254}

# One size token: a fraction ("1/4 inch") or a plain number ("6 mm"). Fractions are the common
# way sheet stock is described, so they must parse. `inches` is listed before `inch` so the
# longer unit wins; the bare quote is the same unit.
_TOKEN = (r'((?P<num>\d+)\s*/\s*(?P<den>\d+)|(?P<whole>\d+(?:\.\d+)?))'
          r'\s*(?P<unit>mm|cm|inches|inch|in|"|thou)')

_LIMIT = re.compile(r"up to\s+" + _TOKEN, re.I)


def _size(match: re.Match) -> tuple[float, str]:
    """Millimetres and the size as written, from a match that contains a _TOKEN group."""
    unit = match.group("unit").lower()
    if match.group("num") is not None:
        value = float(match.group("num")) / float(match.group("den"))
        shown = f"{match.group('num')}/{match.group('den')} {match.group('unit')}"
    else:
        value = float(match.group("whole"))
        shown = f"{match.group('whole')} {match.group('unit')}"
    return value * _MM_PER[unit], shown


def limit_mm(rule_text: str) -> tuple[float, str] | None:
    """The "up to N" limit in a rule's wording: (millimetres, size as written), or None."""
    m = _LIMIT.search(rule_text or "")
    return _size(m) if m else None


def over_thickness(text: str, material: str, rule_text: str) -> tuple[str, str] | None:
    """('12 mm', '6 mm') when `text` states a thickness above the limit in `rule_text`.

    Only a size written as a thickness counts ("12 mm thick", "12 mm plywood", "1/4 inch
    plywood"), so the width or length of a part ("a 300 mm wide plywood panel") is not
    mistaken for one."""
    limit = limit_mm(rule_text)
    if limit is None:
        return None
    limit_value, limit_shown = limit
    words = "|".join(re.escape(w) for w in material.lower().split("_") if len(w) > 2) or "sheet"
    thick = re.compile(_TOKEN + r"(?:[\s-]+thick\b|(?:\s+(?!wide|long|tall|high|by|x)[a-z-]+){0,2}"
                       r"\s+(?:" + words + r"|sheet|board|stock|panel)\b)", re.I)
    for m in thick.finditer(text):
        value, shown = _size(m)
        if value > 100 and "thick" not in m.group(0).lower():
            continue  # "600 mm plywood board" is a length, not a thickness
        if value > limit_value + 1e-9:
            return shown, limit_shown
    return None
