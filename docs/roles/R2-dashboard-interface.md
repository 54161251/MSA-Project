# R2 Dashboard and interface (David)

**Branch:** `r2/dashboard-ui` (merge last)

## What's in it
* `src/msa/ui/Home.py`: sign-in and sign-up (age rules, agreement), then role-based navigation.
  The emergency banner is in the sidebar on every page.
* `src/msa/ui/views/staff.py`: Today (briefing), Approvals (every approval kind with a preview:
  document statements with review flags and removed lines, quiz answers, order lines,
  maintenance tasks, layout proposals), Alerts, Manage equipment (intake, edit, status
  override, quiz guidelines), People & permissions, Maintenance, Reports, Inventory & orders,
  Floor plan editor, Settings & logs (LLM telemetry, audit log).
* `src/msa/ui/views/member.py`: Equipment (status and training badges, PPE, SOP / training /
  materials with sources on hover, machine sessions), My training and quizzes, Ask, Check my
  project, Report an issue, Supplies, Floor plan.
* `src/msa/ui/components/floorplan/index.html`: a Streamlit component in plain HTML/JS (no build
  step): background image, machines coloured by status *with text labels*, clearance zones,
  flagged items, drag to move, keyboard moves (arrows) and rotation (R), save.
* `src/msa/agents/layout/`: Layout agent: searches safe spots for unplaced machines, verifies every
  suggestion with the R3 layout check, and sends it to staff as a `layout_change` approval.
* `src/msa/agents/briefing/`: daily staff briefing (facts first, optional LLM wording).
* `data/makerspace/floorplan.yaml` and `floorplan.png` (the seeded layout includes one
  deliberate E11 warning: a lumber rack in the table saw's outfeed zone).

## Demo
The demo path: member asks and checks a project, staff approves, floor plan shows status.
Accessibility: text badges and labels so status is never colour alone, larger base font, a
list view of the floor plan for screen readers, keyboard controls in the editor.

## Known limits
Uploaded PDFs work only if they contain an embedded image (export a PNG otherwise). The editor
moves and rotates; resizing is done by removing and re-adding an object.
