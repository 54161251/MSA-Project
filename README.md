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

python -m msa.cli seed --reset --approve-all   # build the demo database (about 5 seconds offline)
python -m msa.cli api                          # terminal 1: API on http://127.0.0.1:8000/docs
python -m msa.cli ui                           # terminal 2: app on http://localhost:8501
```

`--approve-all` publishes every drafted document so members can use the machines straight away.
Leave it out (`python -m msa.cli seed --reset`) to start the way a real makerspace would: all
33 drafts (procedure, training guide and materials list for each machine) wait on the staff
Approvals page, and members get almost nothing until staff approve them.

Demo accounts:

| Sign in as | Password | Shows |
| --- | --- | --- |
| `staff@example.org` (from `MSA_ADMIN_EMAIL` in `.env`) | `change-me-now` | Staff pages and member pages |
| `alex.staff@example.org` | `password123` | A second staff account |
| `priya@example.org` | `password123` | Trained member (laser, 3D printers, soldering, drill press) |
| `marcus@example.org` | `password123` | New member, trained on nothing |
| `sam@example.org` | `password123` | Woodworker (drill press, bandsaw, table saw, sewing) |
| `jordan@example.org` | `password123` | 16, guardian-signed waiver |
| Ellie Rivera (12) | none | Under 13s can't sign in; staff see her under People & permissions |

### After pulling new code

The database layout rarely changes, so restarting is enough: stop both terminals (Ctrl+C),
`git pull`, then start `api` and `ui` again. Run `seed --reset` again only if the release notes
or a pull request say the data or the drafting agents changed and you want the new drafts.

### Saving a demo starting point

Set the app up the way the demo should start (for example, seed without `--approve-all` and
approve a few documents), then stop the API and save a copy of the database:

```bash
cp var/msa.db var/demo-start.db         # Windows: copy var\msa.db var\demo-start.db
```

To go back to it after a rehearsal, stop the API, copy it back and start the API again:

```bash
cp var/demo-start.db var/msa.db
python -m msa.cli api
```

Reload the browser and sign in again. Each browser tab keeps its own sign-in, so you can keep a
staff tab and a member tab open side by side; refreshing a tab signs it out.

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
(check the model name against Anthropic's docs). The key must be an API key created under
**Settings → API Keys** at console.anthropic.com (it needs API credit; a claude.ai subscription
doesn't include any). If a call fails, the agent falls back to the deterministic path and the
failure is counted on the staff **Settings & logs** page, so check there that the calls show
`anthropic` with token counts. With Claude, `seed` takes several minutes instead of seconds.

The offline fallbacks always produce an answer, so a passing offline evaluation says nothing
about how the agents behave with a real model; run `python -m eval.run` with Claude before
quoting numbers (see `eval/README.md`).

## What members and staff can do

**Members:** see machines and their approved procedures (Equipment), take quizzes (My
training), ask questions answered only from approved documents (Ask a question), check a
project plan step by step (Check my project), report problems or incidents (Report an issue),
log supplies they used (Supplies) and see the floor plan.

**Staff:** the daily briefing (Today), approve or reject everything the agents draft
(Approvals), handle alerts (Alerts), manage machines, people, maintenance, member reports,
inventory and orders, edit the floor plan, and see the audit trail and LLM use (Settings & logs).

Rules worth knowing for a demo:

* Questions and plans about bypassing a safety feature, unattended use, mains wiring or making a
  weapon or weapon part ("3D print a gun") are refused or sent to staff (E04). Workshop tools
  that share a word, such as a hot glue gun or heat gun, are not flagged.
* Members can only log what they **used**. Adding stock ("restocked", "brought ... from home",
  "donating") is refused for members and done by staff.
* A floor plan change that breaks a clearance, exit-path or safety-equipment rule is saved but
  shown as a red banner with how to fix it, and staff get an alert.

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
* **Escalation codes** E01-E15 are in `src/msa/core/escalations.py`. Staff never need to read
  them: the briefing and the Alerts page describe each one in plain words and sort them by
  urgency (urgent, needs a decision, for information).
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

## Escalation codes

| Code | What staff see | Stops things until staff act |
| --- | --- | --- |
| E01 | Request for a material that isn't allowed on that machine | Yes |
| E02 | Request for a machine with no approved procedure yet | Yes |
| E03 | Question the approved documents couldn't answer | Yes |
| E04 | Dangerous request (e.g. bypassing a safety feature or making a weapon) | Yes |
| E05 | Member tried a machine they aren't trained on | Yes |
| E06 | Request to use a machine that is out of service | Yes |
| E07 | Drafted safety statement without a source | Yes |
| E08 | Drafted document that contradicts the manual or staff notes | Yes |
| E09 | Drafted document that breaks a safety rule | Yes |
| E10 | Incident reported (injury, fire or smoke) | Yes |
| E11 | Floor plan problem | No |
| E12 | Machine spec the manual didn't make clear | Yes |
| E13 | Under-age member without a signed guardian waiver | Yes |
| E14 | System error (an agent or the AI model failed) | Yes |
| E15 | Machine with several reports in 24 hours | No |

## Troubleshooting

* **Ask refuses every question.** Check that the machine's documents are approved
  (Approvals). With Claude connected, check Settings & logs for failed `anthropic` calls.
* **Every `anthropic` call fails with 0 tokens.** The key in `.env` is wrong or not an API key,
  or the account has no API credit (see above).
* **The dashboard opened on port 8502.** An older dashboard is still running on 8501; stop it
  (`lsof -ti :8501 | xargs kill` on macOS) or use the address the terminal prints.
* **"Invalid HTTP request received" or `use_container_width` messages in the `ui` terminal** are
  harmless.

## Contribute

Create a branch for each change and open a pull request against `main`. Run `pytest` and `ruff check .` before merging. Ask the platform lead and affected module owners to review shared contract changes.

## AI use

This code base was generated with Claude (Anthropic) and split into one branch per role.
See `docs/ai_use_log.md` for what was generated, what was checked and the mistakes found.
