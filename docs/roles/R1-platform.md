# R1 Platform and infrastructure (Kaiwen)

**Branch:** `r1/platform` (merge first; every other branch builds on it)

## What's in it
| File | What it does |
| --- | --- |
| `src/msa/core/models.py` | Every table. Append-only tables (`log_entries`, `inventory_txns`, `audit_events`) and frozen report facts are enforced by SQLite triggers in `db.py`. |
| `src/msa/core/contracts.py` | `AgentResult`, `Citation`, `Statement`, `ToolVerdict`, machine and training states, approval kinds (C1-C7). |
| `src/msa/core/auth.py` | bcrypt passwords, tokens, the age rules (under 15 needs an adult account and can't sign in; 15-17 need a guardian waiver), `require_staff` checked on the server (G10). |
| `src/msa/core/services.py` | `request_approval` / `decide_approval`, `notify` with dedup (C4), `escalate`, `set_machine_status` (C5), `audit`, events. |
| `src/msa/core/training.py` | Training state machine (C6) with expiry. |
| `src/msa/core/llm.py` | One LLM wrapper. Offline mode runs each agent's deterministic fallback; Anthropic mode calls Claude and falls back on failure. Telemetry per call. |
| `src/msa/core/orchestrator.py` | LangGraph graph: runs an agent, follows `handoff`s, loop guards (max 4 runs per agent, 10 steps) raise E14; agent crashes become E14. |
| `src/msa/core/registry.py`, `plugins.py` | How other parts plug in without editing platform files. |
| `src/msa/core/escalations.py` | E01-E15 and the high-risk phrase list. |
| `src/msa/core/docs.py`, `textsearch.py` | Approved-document helpers, citation checking, BM25 search. |
| `src/msa/core/scheduler.py`, `seed.py`, `testing.py` | Daily jobs, demo seeding, test helpers. |
| `src/msa/api/` | FastAPI app, platform routes (accounts, equipment, documents, approvals, alerts, settings, audit, usage sessions). |

## Demo
`pytest tests/core` then open http://127.0.0.1:8000/docs and show sign-up, login, `/health`
(lists every plugin found), and an approval being decided.

## Questions you might get
* *Why plugins?* So six people can build in parallel and merge in any order after R1.
* *Where does state live?* In the database, never in the process: approvals, escalations,
  notifications, training history; LangGraph checkpoints per request.
* *How is PII handled?* Birth year only (no date of birth), hashed passwords, member ids (not
  names) in LLM prompts (G7-G9).

## Known limits
No Alembic migrations; SQLite triggers only (a Postgres move needs equivalent rules); token
auth is simple bearer tokens without refresh.
