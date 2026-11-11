# AI use log

Everyone adds entries here (G11): date, role, tool, what it produced, what you changed, and
mistakes you found in AI output.

## 2026-11-05/06: initial build (Claude, Anthropic; all roles)

**What was generated.** With the instructor's approval, Claude generated the whole first
version of the code base from the team's outline and the Assumptions and Working Rules doc:
platform, safety tools, all ten agents, the fictional data set (invented brands and manuals),
the Streamlit UI and floor plan component, tests and evaluation suites. It was then split into
one branch per role. Each commit lists Claude as co-author. Each teammate is responsible for
reviewing, testing and being able to explain the branch they open.

**How it was checked.** 80 automated tests (unit, API, a simulated 30 days of operation); the
four evaluation suites; ruff; the demo database seeded from scratch; every UI page opened in a
headless browser, with screenshots checked by eye, and the main member flows (ask, project
check, report, supplies, quiz) clicked through.

**Mistakes found in the AI's own output during the build** (all fixed):
* The first dependency install hung for 20+ minutes; reinstalled in smaller groups.
* A YAML value `18650` (battery type) was parsed as a number and crashed the matcher.
* Material parsing read the *reason* text too ("...the resin binder releases fumes" made
  "resin" banned on the laser); now only the material clause before the first full stop is read.
* The laser manual's PPE wording didn't satisfy the "never leave unattended" rule, which
  blocked the laser SOP; the fictional manual was reworded.
* Recording LLM telemetry from a second database session deadlocked on SQLite and made seeding
  take minutes; telemetry moved to its own file.
* The quiz page reused a widget key as a session key and crashed (found by the browser test).
* The first evaluation run found real gaps (see `eval/README.md`): changed numbers and flipped
  meanings passed review; chrome-tanned leather was treated as ordinary leather (an unsafe
  answer); "3D printer" didn't match its category; the section name "PPE" wasn't searchable.
* An early version of the negation check flagged verbatim manual lines as unsupported and
  silently removed good statements from five SOPs; caught by re-reading the seeded SOP review
  summaries, fixed by accepting verbatim quotes before the stricter checks.

**Limits to disclose.** The offline suites now pass 100 % only after these fixes, so they are
no longer a held-out test. Safety values in the rule files are placeholders, not reviewed by a
safety professional.

## 2026-11-07: first run with the real LLM (Claude; R4, R5)

David ran the evaluation with an Anthropic key. It crashed: some machines had no approved SOP,
because with the model doing the drafting (instead of copying manual lines word for word):
* citations pointed at section labels that didn't exactly match the manual ("p.2 PPE") and
  quoted paraphrases, so Review treated the lines as uncited (E07);
* Review's rule-based meaning check treated faithful rewording ("keep the lid closed" ->
  "never open the lid while it fires") as a changed meaning;
* removing those lines emptied required sections, so the whole SOP was blocked.

Fixes: Intake now repoints model citations at the real section and takes the quote from the
manual itself; Review lets the model judge rewording (numbers still have to match exactly);
the revise step refills an emptied section with the manual's own cited lines; the evaluation
prints a build report (LLM calls, failures, machines without an approved SOP) and records a
broken suite instead of crashing. Two tests simulate model-style drafts. These were found and
fixed without the key (tests use a scripted model), so the real-model numbers still need a rerun.

## 2026-11-07: second real-LLM run (Claude; R4, R5, R6)

The rerun no longer crashed but showed the real cause: 8 of 11 machines never published an
SOP ("missing required sections"), so material rules, permissions and the review suite all
failed downstream (ask 18/24, project check 12/20, review 2/26). The model's statements were
being thrown away when their shape didn't match exactly (key names, kinds), and paraphrased
material lines ("plywood is fine") weren't recognized as rules. Also, the model's spec answer
could override a footprint clearly stated in the manual (E12 on the resin printer and bandsaw),
and it classified "the fire extinguisher is missing" as a near-miss.

Fixes: model output is coerced instead of dropped; required SOP sections the model leaves out
are filled with the manual's cited lines; the Material rules section and the materials list
always use the manual's exact lines (they become the machine's rules); footprints read from
the spec table can't be overridden; review results are cached so shared statements are judged
once; the triage prompt now says missing safety gear is feedback; `MSA_LLM_DEBUG=on` saves raw
model replies to `var/llm_debug/`. A simulated paraphrasing model now publishes all 11 SOPs.

## 2026-11-07: third real-LLM run (credit ran out)

19 of 39 calls failed because the API account ran out of credit, so this run's scores don't
count. The saved replies (`MSA_LLM_DEBUG`) showed two more bugs in the AI-written code:
* Claude wrote citation versions as `"v1"`; the parser expected a number and silently dropped
  every citation, so every line looked uncited.
* When a dropped line was word-for-word the same as the manual line, the refill step treated
  the manual line as "already rejected" and left the section empty.
Fixed (versions like "v1" accepted; only lines rejected for content stay out; doc-level
problems such as missing PPE terms now go back for revision too). Replaying Claude's real
replies from that run, all 11 SOPs now publish. One of those replies is kept as a regression test.

## 2026-11-07: free LLM options (R1, R5)

Added an `openai_compatible` provider (Ollama / LM Studio locally, or the free tiers of
Google's Gemini API and Groq) with rate-limit retries and optional spacing between calls, and
`python -m eval.run --offline-build`, which builds the demo makerspace without the LLM so a
run needs roughly 100 model calls instead of about 1,000. Results from a different or smaller
model are not results for Claude; report which model produced which numbers.

## 2026-11-07: fourth real-LLM run crashed in maintenance planning

Claude returned the manual line as a number (`"line": 1`) and the maintenance planner assumed
text, which crashed the whole build. Fixed the planner, and fixed the class of bug: every agent
now type-checks model output (strings for booleans, numbers for text, dicts for lists); a
crash in one role's event handler is recorded as E14 instead of stopping the action that
triggered it; and a new end-to-end test runs the whole build and every eval suite with a
model that returns deliberately malformed output.

## 2026-11-07: first complete Claude run

84 build calls, 0 failures, every SOP published, 0 unsafe answers; ask 22/24, project check 19/20,
review 26/26, triage 12/12 (details in `eval/README.md`). The three misses were over-cautious
and came from the AI-written retrieval (duplicates, no stemming) and project-check prompt.
Fixed with regression tests built from Claude's actual replies.

## 2026-11-07: fitted onto Kaiwen's repository skeleton (R1)

Kaiwen (R1) set up the repository with a `src/msa/` package, a starter fixture and test, a
project plan and CI. Claude moved the generated code from `app/` into `src/msa/` (imports
changed from `app.` to `msa.`), kept his project plan, fixture and package file unchanged,
merged his `.gitignore`, CI and README with the generated ones, added a table of where each
contract lives in code to `docs/shared-contracts.md`, and re-sorted the imports in his
starter test so lint passes. CI now installs the package and runs `ruff` and `pytest` (which
also runs his `unittest` test) on Python 3.11. All 94 tests and the offline eval pass; each
role's part was replayed in merge order on top of his commits with tests passing at each step.

## 2026-11-07: R1 repository import (Codex)

Codex read the R1 handoff instructions, applied the supplied R1 files to the
existing repository on the designated branch, and prepared the pull request.
The other role packages are queued for their owners in the agreed merge order.

## 2026-11-08: R5 branch committed (Claude; R5, Lluc)

Claude (Sonnet 5.5, in a Claude chat session linked to Lluc's GitHub fork) took the R5 package
(Review agent, Ask agent, evaluation suites, tests: 25 files, about 1,375 lines, itself
generated by Claude earlier) and followed its `HOW_TO_COMMIT.md`: created the branch
`r5/review-ask-eval` from the current `main` (which already contained R1, R3 and R4), copied the
files in (none overwrote an existing file), committed with the supplied message and pushed.

**Checked by Claude after copying.** The full test suite passed (75 tests) and `ruff check`
reported no problems. The evaluation suites (`python -m eval.run`) were not run in this session,
because they need the configured LLM or the offline build, so no new evaluation numbers are
claimed here.

**Not yet done by Lluc (fill in before the pull request is merged).** Read through the Review
and Ask agents and the evaluation suites, click through the R5 demo from the README quick start
(ask as "priya", the Approvals page, `python -m eval.run`), and record here what was reviewed,
changed or found wrong.

## 2026-11-11: R5 hardening pass (Claude Sonnet 5.5, in a chat session; R5, Lluc)

Lluc asked Claude to go over the R5 code (Review agent, Ask agent, escalation catalog, evaluation
sets) and make it behave correctly. Claude probed Ask with about 50 hostile or odd questions and
Review with tampered and malformed drafts, then fixed what failed. Found and fixed:
* Ask: emergencies without a listed word ("I got electrocuted", "somebody fainted", "there is
  smoke in the room", "blood everywhere") got a plain refusal with no 911 message or staff alert.
* Ask / catalog: high-risk requests ("get around the interlock", "override the e-stop", "go get
  lunch" while the laser runs, "guard off", "hack the firmware") were answered or sent to the wrong
  code (E01/E03 instead of E04).
* Ask: a "Yes" for a material dropped the manual's limit ("up to 6 mm"); "can I laser carbon
  fiber" skipped the rules tool because it had no cut verb; "what do I do first?" skipped the
  training check.
* Review: crashed on malformed drafts (missing or non-dict citations, non-text statements); did
  not notice a changed unit ("6 mm" -> "6 cm").
Added 12 `rt-*` evaluation cases and about 70 regression tests. After the fixes: 164 tests pass,
`ruff` is clean, and the offline suites pass 100 % (ask 36/36). The offline suites were not run with
a real model in this session, and the new cases came from the probe, so they are not a held-out test.
Not yet reviewed by Lluc.
