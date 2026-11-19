# R3 Safety rules and Project Check (Tomas)

**Branch:** `r3/safety-rules` (after R1)

## What's in it
* `src/msa/safety_tools/rules/global.yaml`: material names and synonyms, category-wide bans (e.g.
  PVC, polycarbonate, ABS and chrome-tanned leather on lasers), baseline PPE, SOP structure
  rules, cross-step rules, layout distances and the 5x5 risk matrix. **Placeholder values**:
  a real makerspace must review them.
* `src/msa/safety_tools/tools.py`: the deterministic tools, all returning `ToolVerdict`:
  `check_material`, `required_ppe`, `can_use`, `check_sop_rules`, `risk_score`,
  `check_layout` / `evaluate_layout` (clearance zones with rotation, grid search for a
  0.71 m path to an exit, extinguisher / eyewash / first-aid distances).
* `src/msa/safety_tools/server.py`: the same tools as an MCP server (`python -m msa.cli mcp`).
* `src/msa/safety_tools/thickness.py`: reads a material rule's "up to 6 mm" limit and checks a
  plan or question against it, including inch fractions ("1/4 inch" is 6.35 mm, over a 6 mm
  limit). Shared by Project Check and Ask so they cannot disagree (see the fixes below).
* `src/msa/agents/project_check/agent.py`: splits a plan into steps (LLM or clause splitting),
  re-scans every step deterministically, runs the tools, applies cross-step rules, returns
  `clear`, `clear_with_conditions` or `needs_staff` and opens a staff approval when blocked.

## Demo
Member "priya": *Laser cut a box from 3 mm plywood* (clear with conditions), then *Laser cut a
PVC sign* (needs staff, E01), then *Spray paint the box next to the laser, then cut the lid*
(cross-step fire risk, E04).

## Questions you might get
* *Why not let the LLM judge safety?* LLMs miss hazards (LabSafety Bench). The LLM only splits
  text into steps; the rules decide, and unknown materials escalate (G2, G3).
* *What if the LLM drops a material?* The step text is re-scanned deterministically; a test
  covers exactly this.

## Known limits
Keyword matching can miss unusual wording; materials not in the synonym list escalate (safe,
but noisy). The rules match wording, not meaning: "leather (chrome tanned)" needed its own
synonym, and "print a copy of the SOP" used to be read as 3D printing until the bare `print a`
keyword was removed.

## Layout rotation
Clearance sides are assigned in 90 degree steps (front, left, back, right), and `w`/`h` are
stored already rotated, because the floor-plan editor and the save route swap them. Any other
angle would be checked as if the machine faced the nearest right angle, so `LAY-ROTATION` now
reports it instead of guessing.

## Changes made 2026-11-16
A review pass over R3 found four wrong results. All are fixed with tests in
`tests/safety_tools/test_r3_fixes.py` (29 cases):

* **Ask answered "Yes" to an over-limit thickness.** "Can I laser cut 12 mm plywood?" said
  "Yes ... the manual says up to 6 mm", while Project Check escalated the same plan. The
  thickness parser lived in the Project Check agent, so only it could use it. Moved to
  `safety_tools/thickness.py`; Ask now refuses and raises E01. Inch fractions are supported.
* **Cross-step rules fired on machines the plan never combined.** "Rip pine on the table saw,
  then laser a cardboard tag" triggered the acrylic-blade rule. Rules now accept optional
  `materials` / `processes` / `any_triggers` conditions; the acrylic rule requires acrylic.
  Added `X-RESIN-HEAT` and `X-SAND-LASER`.
* **`print a` was a 3D-printer keyword**, so "Print a copy of the SOP" got 3D-printer PPE.
  Removed; a filament or resin name now implies its machine (`material_categories`).
* **45 degree rotations were checked as 0.** Now reported as `LAY-ROTATION`.

Still open: conditions attached to an "allowed" material ("mild steel **with cutting fluid**")
are not surfaced to the member; `ask_staff` has no test; `risk_score` is exposed but unused.
