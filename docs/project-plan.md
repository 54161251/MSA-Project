# Project plan

The Makerspace Safety Agent will help members find equipment, check their training and machine status, consult approved guidance, and submit project plans for review. Staff will manage equipment, review proposed procedures, and resolve escalations.

## Responsibilities

| Role | Owner | Scope |
| --- | --- | --- |
| R1 Platform and infrastructure | Kaiwen | Orchestrator, shared state and contracts, API and database, append-only logs, accounts and roles, MCP scaffolding, CI and integration |
| R2 Dashboard and interface | David | Staff dashboard, member portal, floor plan overlay, approval queues, staff briefing and demo |
| R3 Safety rules and Project Check | Tomas | Deterministic MCP checks for materials, PPE, clearance, prerequisites and machine status. Project Check agent |
| R4 Equipment knowledge | Jad | Equipment Intake, SOP and training drafts, Quiz agent and fictional makerspace data |
| R5 Review, Q&A and evaluation | Lluc | Review and Ask agents, escalation catalog, adversarial test cases and evaluation report |
| R6 Operations agents | Christina | Maintenance, Triage and Inventory agents |

Each owner writes tests for their module. R5 coordinates the shared evaluation. R1 and the affected owners review changes to shared contracts.

## Workflow and safety requirements

- Equipment Intake drafts procedures, training materials and allowed-material lists from manuals and specs. Review checks citations and contradictions. Staff approve publication before members see the content.
- Ask answers from approved sources and cites them. Unsupported or risky questions and questions from untrained members go to staff. Quiz uses approved materials and follows the training policy configured by staff for each machine.
- Project Check maps plan steps to machines and materials. Deterministic MCP tools check materials, PPE, clearance, training prerequisites and machine status. Unknown machines or materials and risky plans go to staff.
- Maintenance, incident and feedback workflows feed a shared machine status used by Ask, Project Check and the floor plan. Staff record maintenance completion and resolve incident reports. Corrections to maintenance, inspection and incident logs are new entries linked to the originals.
- Triage groups duplicate reports and surfaces emergency information immediately for emergencies. Inventory can log usage and draft replenishment orders for staff approval.
- Layout can suggest equipment placement. Deterministic checks flag clearance, exit-path and safety-equipment issues, and staff approve the layout. The staff briefing summarizes open work without changing records.
- Staff control access, publication and purchasing. Use fictional member records for development. The proposal leaves guardian sign-off details for the user agreement to confirm.

## Milestones

| Due (2026) | Deliverable |
| --- | --- |
| Nov 8 | Proposal |
| Nov 11 | Repo skeleton, shared contracts, mocks and fictional makerspace data |
| Nov 15 | Core platform, accounts, API and orchestration, safety tool scaffolding, manual intake and dashboard shell |
| Nov 22 | Specialist agents tested separately and first SOPs approved through Intake and Review |
| Nov 29 | Integrated workflows, Project Check and floor plan checks. Feature freeze |
| Dec 2 | Evaluation results, critical fixes, recorded demo, slides and rehearsal |
| Dec 3 | Live presentation |
| Dec 4 | Final report, AI-use log and tagged release |

If time is tight, cut automatic layout suggestions, the staff briefing and usage-hour maintenance first. Keep Review, citations and escalation.

## Evaluation

Measure how often Review catches planted SOP errors, how often Ask correctly escalates unsupported or risky questions, Project Check's detection and false alarm rates on labeled plans, and how accurately Triage groups duplicate reports.
