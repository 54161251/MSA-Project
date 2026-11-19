"""Escalation catalog (Assumptions doc: Escalation catalog).

Add a code here through a PR first; agents never invent codes. Ships in the platform branch
because every role raises these; R5 maintains the list.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Code:
    code: str
    title: str
    blocking: bool
    member_message: str


CATALOG: dict[str, Code] = {
    c.code: c
    for c in [
        Code("E01", "Material not in this machine's material rules", True,
             "That material isn't on the approved list for this machine. Please ask staff."),
        Code("E02", "Equipment not registered or has no approved SOP", True,
             "This equipment doesn't have an approved procedure yet. Please ask staff."),
        Code("E03", "No approved source supports the answer", True,
             "I couldn't find this in the approved documents. Please find a staff member."),
        Code("E04", "High-risk request", True,
             "This could be dangerous, so a staff member needs to help you in person."),
        Code("E05", "Member lacks required training or prerequisite", True,
             "You need to complete the required training before doing this."),
        Code("E06", "Machine not usable", True,
             "This machine is out of service right now."),
        Code("E07", "Safety claim in a draft has no citation", True, ""),
        Code("E08", "Draft contradicts the manual or staff notes", True, ""),
        Code("E09", "Draft breaks a safety rule", True, ""),
        Code("E10", "Incident reported", True,
             "Staff have been alerted. If anyone is hurt, call emergency services now."),
        Code("E11", "Layout breaks a clearance, exit-path or safety-equipment rule", False, ""),
        Code("E12", "Low confidence extracting a spec from a manual", True, ""),
        Code("E13", "Minor account without guardian link or signed waiver", True,
             "A guardian-signed waiver is needed before machine access."),
        Code("E14", "Agent loop guard hit, or a tool or LLM failed", True,
             "Something went wrong. Please ask a staff member."),
        Code("E15", "Several reports about one machine within 24 hours", False, ""),
    ]
}

#: high-risk categories for E04 (used by Ask and Project Check)
HIGH_RISK_PATTERNS: dict[str, list[str]] = {
    "bypass_safety": ["bypass", "override the interlock", "disable the interlock", "defeat",
                      "remove the guard", "without the guard", "take off the guard",
                      "disable the sensor", "tape the switch", "jumper the", "disable the lid",
                      "get around the", "turn off the safety", "turn off the interlock",
                      "turn off the sensor", "turn off the guard", "disable the e-stop",
                      "disable the e stop", "disable the estop", "override the e-stop",
                      "override the e stop", "override the estop", "override the safety",
                      "override the guard", "guard off", "guard removed", "with the lid open",
                      "lid open while", "trick the sensor", "trick the interlock",
                      "cheat the interlock", "tape down the", "tape the lid", "tape over the",
                      "prop the lid", "hold down the interlock", "without the riving knife",
                      "without the guard", "without the blade guard", "remove the riving knife",
                      "remove the guard", "remove the blade guard", "take off the guard",
                      "take the guard off", "defeat the"],
    "modify_machine": ["modify the machine", "modify the laser", "rewire", "open the power supply",
                       "upgrade the tube", "change the firmware", "replace the laser tube",
                       "hack the", "flash custom firmware", "overclock", "modify the printer",
                       "modify the saw", "modify the cnc"],
    "mains_electrical": ["mains", "120v", "240v", "230v", "wall outlet wiring", "high voltage",
                         "rewire the plug", "line voltage"],
    "unattended": ["unattended", "leave it running", "walk away", "overnight", "leave the room",
                   "go home", "go get lunch", "go to lunch", "while i'm away", "while i am away",
                   "while i go", "step away from the", "leave the machine", "leave it on",
                   "leave the laser", "leave the printer", "leave the saw"],
    "fire_or_chemical": ["acetone near", "set fire", "flammable spray", "solvent near the laser",
                         "pour resin down"],
}


def get(code: str) -> Code:
    if code not in CATALOG:
        raise KeyError(f"Unknown escalation code {code!r}; add it to the catalog first")
    return CATALOG[code]


def match_high_risk(text: str) -> list[str]:
    """Return the high-risk categories a piece of text matches (deterministic, lowercase)."""
    t = text.lower()
    return [cat for cat, pats in HIGH_RISK_PATTERNS.items() if any(p in t for p in pats)]
