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
but noisy). Layout check treats rotation in 90 degree steps only.
