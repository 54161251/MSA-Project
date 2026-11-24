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

## 2026-11-19: R6 review and fixes (Claude, Sonnet 5.5; R6, Christina)

Christina asked Claude (in the Claude desktop app, working on her local clone of the repo) to
review the three R6 agents against the Assumptions doc and fix what it found, with a test for
each fix.

**What Claude produced.** A review of `maintenance/`, `triage/` and `inventory/` that listed six
problems, ten new tests in `tests/agents/test_operations_fixes.py` (one or more per problem), the
fixes in the three `agent.py` files, one changed test in `test_operations.py` (a member restocking
now uses a staff account, per A2) and a short rules section in `docs/roles/R6-operations.md`.

**Later the same day (Review on maintenance plans, agreed with Lluc).** Claude added `review_plan`
and `review_plans` to `src/msa/agents/review/agent.py` (R5's folder, with Lluc's agreement) and
changed `draft_plans` so every maintenance draft goes through them before an approval is created;
four more tests. Its first idea was to hand the draft to Review through the orchestrator, but the
direct callers (the SOP-published event, the re-draft route, the tests) don't follow handoffs, so
the plans would have been stuck as drafts; Claude switched to a direct function call.

**How it was checked.** Claude could not run the tests itself (no working Python environment on
its side). Christina ran the new tests before the fixes: 9 failed and 1 passed, exactly as Claude
had predicted from reading the code, so each problem was reproduced before it was fixed.
Results after the fixes and the Review change (run by Christina on 2026-11-19): `ruff check .` clean;
`pytest` 103 passed,
run with `--ignore=tests/safety_tools/test_mcp_server.py` because that file cannot import the
`mcp` library on her Mac (a local `cryptography`/OpenSSL mismatch in her Anaconda, unrelated to
R6). `python -m eval.run` (offline mode, so the deterministic fallbacks, not a real LLM): ask 24/24,
project_check 20/20, review 26/26, triage 12/12 with incident_recall 100% and grouping_accuracy 100%.

**What Claude got wrong or changed its mind about.** Its first review also suspected that
resolving an incident would break the append-only rule; reading `db.py` showed that only the
report's facts are frozen, not its status, so that was dropped.

Results were reviewed by Christina.

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

## 2026-11-15: Cross-team check and fixes (Claude Fable 5.2, in a chat session; started by Lluc)

Lluc gave Claude the six role packages (R1 to R6) and asked it to check that the repository
matches them, fix what is wrong and improve weak spots. Claude compared every file in the six
packages with `main` (all present; the only differences are the later R5 hardening and R4
improvement pull requests), ran the tests, lint and offline evaluation, called every API route as
a guest, a member and a staff user with valid and malformed input, and probed Project Check,
Triage and Ask with about 80 hostile or odd inputs. Access control held on every route. Found and
fixed:
* Project Check (R3): cleared material thicker than the manual allows (12 mm plywood on a 6 mm
  limit); answered "Your plan looks fine" to a plan in which it recognized no machine or material;
  read "leather (chrome tanned)" as plain leather.
* Escalation catalog (R1/R5): "tape down the lid switch", "without the riving knife" and "take the
  guard off" were not high-risk (E04).
* Triage (R6): a small flame was filed as feedback; "sparks came out of the motor" was filed as
  out of stock; "doesnt work", noises and leaks had no issue type.
* Ask (R5): narrowed emergency words Claude itself added on 2026-11-11 that would raise false
  alarms ("crushed velvet", "filament got caught in the extruder", "smoke from the laser").
* API: adding or renaming an inventory item to an existing name (R6) and adding a floor plan
  object with an unknown machine id (R2) returned a server error instead of a clear message.
Added 34 regression tests and 9 evaluation cases. After the fixes: 204 tests pass, `ruff` is
clean, and the offline suites pass 100 % (ask 36, project check 26, review 26, triage 15). Not run
with a real model in this session. These changes touch other members' parts (R1, R2, R3, R6) and
have not been reviewed by those owners or by Lluc.

## 2026-11-16: R3 review and fixes (DeepSeek V4.1 Flash, in a Cursor chat; R3, Tomas's part)

Reviewed the R3 package (safety tools, rule tables, Project Check, layout check) against the
fictional manuals, then **ran the seeded makerspace and probed it** rather than reasoning from the
code, which corrected one claim: the category bans I expected to be missing from `global.yaml`
(steel on the CNC, metal on the saw, nylon on the FDM printer) are present as machine-level
`MaterialRule` rows built by Intake, and they fire correctly.

Four real problems were found and fixed:
* **Ask and Project Check gave opposite answers about the same rule.** "Can I laser cut 12 mm
  plywood?" was answered "Yes ... the manual says up to 6 mm", while Project Check escalated the
  same plan with `MAT-THICKNESS`. The parser lived in the Project Check agent, so Ask could not
  use it. Moved to `safety_tools/thickness.py` and called from both; Ask now refuses with E01.
  Inch fractions ("1/4 inch") are supported — a fresh eval case had been written for exactly this
  on 2026-11-11 and recorded as fixed, but only Ask's prose had been fixed, not the check.
* **Cross-step rules ignored materials.** "Rip pine on the table saw, then laser cut a cardboard
  tag" fired `X-LASER-ACRYLIC-SAW`, a rule about cutting the same acrylic on both machines. Rules
  now take optional `materials` / `processes` / `any_triggers`; the acrylic rule requires acrylic.
  Added `X-RESIN-HEAT` and `X-SAND-LASER`.
* **`print a` was a 3D-printer keyword**, so "Print a copy of the SOP" was checked as 3D printing
  and given "do not touch the nozzle" as its safety conditions. Removed; a filament or resin name
  now implies its machine, so "print a phone stand in PLA" still works.
* **A 45 degree rotation was checked as 0 degrees.** Clearance sides only exist in 90 degree
  steps, so `LAY-ROTATION` now reports an angle it cannot model. Note `w`/`h` are stored already
  rotated (the editor swaps them), so the footprint itself was correct — my first reading of this
  was wrong and the probe showed it.

The full test suite passes (234 tests, 29 of them new in `tests/safety_tools/test_r3_fixes.py`),
`ruff` is clean, and the offline suites still pass 100 % (ask 36, project check 26, review 26,
triage 15). The new tests were written after the fixes, so they are not a held-out set. Not run
with a real model. Recorded in `docs/roles/R3-safety-rules.md`. Still open and not fixed:
conditions attached to an "allowed" verdict ("mild steel **with cutting fluid**") are not shown
to the member, `ask_staff` has no test, and `risk_score` is exposed but unused. Not reviewed by
Tomas.

## 2026-11-18: Ask retrieval fix after the first real-model demo (Claude Opus 5.5, chat session; R5, Lluc)

Lluc ran the demo locally with Claude for the first time and found that Ask refused basic laser
questions. With Claude, Lluc ran diagnostic scripts on his machine that printed each Ask step and
Claude's raw replies; they showed the cause was Ask's own passage filter (the machine's name and
filler words counted as matches, so the relevant lines were dropped before Claude saw them), plus
an all-or-nothing answer rule. Claude changed `src/msa/agents/ask/agent.py`: only subject words
count in the filter, Claude may give a partial answer with a note to ask staff, and refusals keep
their trace (they used to lose it, which hid the cause). A first, looser version of the filter
let off-topic questions through offline and was replaced before committing.

Checked: 6 new regression tests built from the recorded Claude replies, the full test suite and
`ruff` pass, offline suites 100% (ask 37/37, unsafe answers 0), and a 57-question hostile probe
gives the same results as before except one question the documents do not answer, now refused.
**Not yet re-run with Claude after the fix**; Lluc to confirm on his machine before merging.


## 2026-11-20: dashboard colour scheme (R2, David)

Claude restyled the Streamlit app with the team's presentation colours, using colour to show who
each page is for: maroon for staff-only pages, coral for member pages, yellow for anything waiting
on a staff decision, and black-and-yellow hazard stripes for emergency notices. Page titles got a
coloured header with an icon and a "Staff only" / "Member page" chip, the sidebar sections and icons
follow the same colours, and a colour key sits in the sidebar. Machine and training status badges
keep green / orange / red (with icons and words), because those colours carry safety meaning;
finding icons are coloured by severity. The theme is in `.streamlit/config.toml`; the page-level
styles are in `src/msa/ui/common.py`. Checked by screenshots of each page as staff and as a member;
lint and all tests pass. Reviewed by David.

## 2026-11-24: Changes after the demo rehearsal (Claude Opus 5.5, chat session; requested by Lluc)

Lluc asked for six changes after rehearsing the class demo. Claude made them on one branch:
* **Weapons (R1/R5 catalog, R3 Project Check, R5 Ask).** A new `weapon` high-risk category
  ("3D print a gun", "lower receiver", "silencer", "bomb"...) is refused in Ask with its own
  message and flagged in Project Check (`RISK-WEAPON`, E04). Whole-word matching with exceptions,
  so "hot glue gun", "heat gun", "nail gun", "begun", "bath bomb" are not flagged. Knives and
  swords are deliberately *not* included (common, legitimate makerspace projects); staff can
  add them.
* **Floor plan editor (R2).** The layout check now sits above the plan, each problem is a red or
  amber box with a "How to fix" line instead of a rule code, and after a save, add or remove a
  banner says whether the change created or fixed problems.
* **Daily briefing (R2).** Problems are described in plain words, most urgent first; the model
  never sees codes or ids, and a summary that still contains a code is replaced by the
  rule-based one. The Today page lists open alerts and waiting approvals in plain words.
* **Alerts page (R2, R1 API).** Alerts grouped as Urgent / Needs a decision / For information,
  each with a plain title, who or what it's about (names, not ids), the details, time and a
  Resolve box; the code is shown small. `/escalations` returns these fields; staff
  notifications use the same wording.
* **Supplies (R6).** Members could already not add stock with words like "restocked", but
  "I brought 3 sheets of plywood from home", "donating" and "gave back" were logged as *use*
  (stock went down). These are now recognised as adding (refused for members), and a member
  sentence with no "used"-type word asks them to rephrase. Also fixed a quantity bug: "about
  20 g of PLA" was read as 2.
* **README.** Demo accounts, starting without `--approve-all`, restarting after a pull, saving
  and restoring a demo starting point, what members and staff can do, a plain-language code
  table and troubleshooting.

Checked: 37 new tests (`tests/test_demo_feedback.py`), full suite 291 passed, `ruff` clean,
offline evaluation 100% with 0 unsafe answers (2 new cases), every changed page rendered with
Streamlit's test harness against a running API. Not checked with Claude connected, and the
pages were not looked at in a real browser. These touch R2, R3 and R6 code; those owners
should review the pull request.
