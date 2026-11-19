# R6 Operations agents (Christina)

**Branch:** `r6/operations` (after R5)

## What's in it
* `src/msa/agents/maintenance/`: drafts plans from the manual's Maintenance section ("Every 8 hours
  of use", "Every 30 days", "Weekly") with citations; staff approve. The daily check makes a
  task due by days or usage hours, sends one notification per task, sets `maintenance_due`,
  and `out_of_service` after the grace period (7 days). Logging the work puts the machine back.
  Log entries are append-only; mistakes get correction entries.
* `src/msa/agents/triage/`: classifies reports (incident, near-miss, feedback; emergency words
  always mean incident), takes a machine out of service on an incident, groups duplicate
  feedback into one notification with a count, raises E15 for 3+ reports in 24 h, and suggests
  a fix from approved documents only.
* `src/msa/agents/inventory/`: logs "used two sheets of 3 mm acrylic" in plain language, asks which
  item when unsure, forecasts run-out, and drafts one order per supplier at the reorder point
  (never places orders). CSV and Excel export.
* `data/makerspace/inventory.yaml`, `eval/suites/triage.py`, `eval/sets/triage.json`.

## Rules the code enforces (added 2026-11-19)
* Maintenance: the manual's own line decides each task's interval and citation. The AI can only
  add a task if the manual line states the same interval, and it cannot drop or change one.
  Tasks counted in hours of use also lock the machine after the 7-day grace period (measured from
  the first check that saw them due). Re-drafting plans keeps each task's last-done date and hours.
* Review: every drafted maintenance plan goes through the Review agent before staff see it
  (`review_plans` in `agents/review/agent.py`, also reachable as `orchestrator.run(db, "review",
  {"equipment_id": ..., "plan_ids": [...]})`). It checks that the citation resolves, that the cited
  line names the task (E07) and that the interval matches the manual line (E08). Any flag means no
  approval is created and staff get an escalation; plans already approved stay in force. A clean set
  creates one approval with a reviewer summary.
* Triage: emergency words are decided before any LLM call (G5). A report about a high-risk act
  (bypassing a guard, mains wiring, unattended running...) raises E04 and gets no suggested fix.
* Inventory: only staff can add stock (A2); members log use only. An item already on a draft or
  an approved order that has not been marked received is not ordered a second time.

## Demo
Member: report "the laser cutter is not firing" three times from different accounts and show
one notification with count 3. Report an injury and show the emergency banner and the machine
going out of service. Log supplies until an order draft appears in Approvals. Staff: Today,
Run the daily check now.

## Known limits
Duplicate grouping is by machine and issue type, so two different faults of the same type on
one machine group together. Forecasts use a simple 30-day average.
