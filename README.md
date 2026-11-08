# Makerspace Safety Agent

ENMGT 5405 final project. A multi-agent safety and operations system for makerspaces. It helps members find equipment, check their training and machine status, find staff-approved guidance and check project plans. Staff manage equipment, review drafted procedures and handle escalations. **Agents draft; staff decide.** Every safety decision goes through a human approval.

The demo runs on a fictional makerspace ("Fernwood Community Makerspace"); every brand, manual, member and supplier in `data/` is invented.

- [Project plan](docs/project-plan.md) covers ownership, workflows, evaluation, and milestones.
- [Shared contracts](docs/shared-contracts.md) describes how modules connect.
- [Revised proposal](https://docs.google.com/document/d/1q-CFBuJOqFvfAmSzHHrwpZtl8f3_ZWZ9Pgnfu12R8g8/edit?tab=t.0) is the source for the project plan.

## Repository layout

| Path | Purpose |
| --- | --- |
| `src/msa/` | Shared platform and application modules (one folder per agent) |
| `data/makerspace/` | Fictional makerspace: machines, manuals, members, inventory, floor plan |
| `eval/` | Evaluation suites and labeled test sets |
| `docs/` | Shared contracts, workflow decisions, role notes and the AI-use log |
| `fixtures/` | Starter fixture for the equipment and floor plan link test |
| `tests/` | Module and integration tests |
| `.github/workflows/ci.yml` | Lint and tests on pushes to main and pull requests targeting main |

## Quick start

Needs Python 3.10 or newer (CI uses 3.11).

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate     macOS/Linux: source .venv/bin/activate
pip install -e ".[dev]"
copy .env.example .env        # macOS/Linux: cp .env.example .env

python -m msa.cli seed --reset --approve-all   # build the demo database (about 5 seconds)
python -m msa.cli api                          # terminal 1: API on http://127.0.0.1:8000/docs
python -m msa.cli ui                           # terminal 2: app on http://localhost:8501
```

Sign in as staff with the `MSA_ADMIN_EMAIL` / `MSA_ADMIN_PASSWORD` from `.env`
(default `staff@example.org` / `change-me-now`), or as a member: `priya@example.org`
(trained on several machines), `marcus@example.org` (new member) or `jordan@example.org`
(16, guardian-signed waiver). Member passwords are `password123`.

Run the checks:

```bash
pytest            # unit and integration tests, including a simulated 30 days
ruff check .
python -m eval.run   # evaluation suites (see eval/README.md)
```

### Offline mode and the real LLM

By default `MSA_LLM_PROVIDER=offline`: every LLM step uses its deterministic fallback, so the
whole system, the tests and the demo work with no API key and give repeatable results. To use
Claude, set `MSA_LLM_PROVIDER=anthropic`, `ANTHROPIC_API_KEY` and `MSA_LLM_MODEL` in `.env`
(check the model name against Anthropic's docs). If a call fails, the agent falls back to the
deterministic path and the failure is counted on the staff Settings page.

## How it fits together

```
Streamlit UI (R2) ──HTTP──> FastAPI + LangGraph orchestrator (R1)
                                  │ routes each request to an agent, follows handoffs,
                                  │ loop guards (E14), checkpoints state
            ┌─────────────────────┼──────────────────────────┐
   Knowledge agents         Operations agents          Check & display agents
   Intake, Quiz (R4)        Maintenance, Triage,       Project Check (R3)
   Review, Ask (R5)         Inventory (R6)             Layout, Briefing (R2)
            └─────────────────────┼──────────────────────────┘
          Safety tools (R3): deterministic checks, also served over MCP
          Database (R1): append-only logs, approvals, escalations, audit trail
```

* **Plugins, not edits.** Each part lives in its own folder with a `plugin.py` that registers
  its agent, routes, approval handlers, seed data and daily jobs (`src/msa/core/registry.py`).
  The platform discovers them at start-up, so parts merge without editing each other's files.
* **Contracts** (`src/msa/core/contracts.py`): every agent returns an `AgentResult`; every safety
  statement carries `Citation`s; machine status and training states have fixed values.
* **Escalation codes** E01-E15 are in `src/msa/core/escalations.py`.
* **Human checkpoints** are `Approval` rows (publish a document, approve a quiz, competency
  check, maintenance plan, order draft, layout change, project review), all handled on the
  staff Approvals page.

## Where each part lives

| Role | Folders | Short description |
| --- | --- | --- |
| R1 Platform | `src/msa/core`, `src/msa/api`, `src/msa/cli.py`, `tests/core` | Data model, auth and age rules, services, LLM wrapper, orchestrator, scheduler, API |
| R3 Safety rules & Project Check | `src/msa/safety_tools`, `src/msa/agents/project_check`, `tests/safety_tools` | Deterministic tools (materials, PPE, permission, layout, SOP rules, risk), MCP server, Project Check agent |
| R4 Equipment knowledge | `src/msa/agents/intake`, `src/msa/agents/quiz`, `data/makerspace` (machines, manuals, members) | Intake agent (manual to cited SOP, training guide, materials list), Quiz agent, fictional data set |
| R5 Review, Q&A, evaluation | `src/msa/agents/review`, `src/msa/agents/ask`, `eval/` | Review agent, Ask agent, evaluation suites |
| R6 Operations | `src/msa/agents/maintenance`, `src/msa/agents/triage`, `src/msa/agents/inventory` | Maintenance plans and daily check, report triage, inventory and order drafts |
| R2 Dashboard & interface | `src/msa/ui`, `src/msa/agents/layout`, `src/msa/agents/briefing` | Staff dashboard, member portal, floor plan component, Layout and Briefing agents |

Each role's notes (what it does, how to demo it, known limits) are in `docs/roles/`.

## Differences from the Assumptions and Working Rules doc

* Tables are created with SQLAlchemy `create_all` (no Alembic migrations yet).
* The escalation catalog ships with the platform (R1) because every part raises codes; R5
  still maintains it.
* Agents call the safety tools in-process; the same functions are also exposed by the MCP
  server (`python -m msa.cli mcp`) for outside clients.
* LLM telemetry is stored in its own small SQLite file so it never blocks the main database.
* The data set has 11 machines (the doc's list names two 3D printers).

## Contribute

Create a branch for each change and open a pull request against `main`. Run `pytest` and `ruff check .` before merging. Ask the platform lead and affected module owners to review shared contract changes.

## AI use

This code base was generated with Claude (Anthropic) and split into one branch per role.
See `docs/ai_use_log.md` for what was generated, what was checked and the mistakes found.
