# Evaluation (R5)

```
python -m eval.run                 # all suites, offline mode
python -m eval.run --suite review  # one suite
```

Set `MSA_LLM_PROVIDER=anthropic` and `ANTHROPIC_API_KEY` in `.env` to run the same cases
against Claude. Results go to `eval/results/latest.json` (not committed).

| Suite | What it tests | Key metrics |
| --- | --- | --- |
| `review` | 20 SOP drafts with one planted error each (missing PPE or sections, banned material listed as allowed, uncited claims, wrong numbers, flipped meaning, contradicting a staff note) plus 6 clean drafts | catch rate, false-alarm rate |
| `ask` | 24 questions: answerable, material yes/no, unknown materials, untrained how-to, high-risk, emergencies, off-topic | correct refusals, correct answers, **unsafe answers** |
| `project_check` | 20 project plans: 8 safe, 12 unsafe (banned materials, missing training, unattended use, risky combinations) | catch rate, false-alarm rate |
| `triage` | 12 cases (17 reports): duplicate and distinct issues, incidents, near-misses, look-alikes such as "burn marks" or "fire extinguisher is missing" that are NOT incidents | incident recall, grouping accuracy |

## Honest history (G13)

The cases and their expected results were written before the first run. The first offline run
found real gaps, which were fixed in the system, not in the cases:

| Suite | First run | Problems found | Fix |
| --- | --- | --- | --- |
| review | 22/26, catch 80% | Word overlap missed changed numbers ("6 mm" -> "50 mm"), flipped meaning ("glasses are optional", "hold parts by hand") | Review now checks numbers, negation and risky qualifiers against the best-matching source sentence |
| ask | 20/24, 1 unsafe answer | Section names weren't searchable ("PPE"); "3D printer" didn't match the "3d print" keyword; "what materials are allowed" was treated as a yes/no material question; chrome-tanned leather was treated as ordinary leather (**unsafe**) | Index statements with their section; keyword suffixes; stricter yes/no pattern; new `leather_chrome_tan` material, banned on lasers |
| project_check | 18/20 | "maple" wasn't covered by "Allowed: wood" (false alarm); chrome leather passed | Parent materials (hardwood -> wood); same chrome leather rule |

One expectation changed: `chrome-leather-laser` was written as E01 (unknown material). After
the new rule it is a firm "no" (banned), which is a stricter refusal, so the case now expects `no`.

After the fixes all offline suites pass. **That means these cases are now training data, not a
held-out test.** Before the final report, write a fresh set of cases that nobody has run yet and
report those numbers too. Running with the real LLM will also give different numbers; report both.

Triage (added with R6) passed 12/12 on its first run, but treat that with care: its cases were
written after the incident word list had already been narrowed (words like "fire" and "burn"
alone matched harmless reports such as "fire extinguisher is missing" or "burn marks on
plywood"), so the look-alike cases were chosen knowing that fix existed.

## Results with Claude (2026-11-07)

First complete run with `MSA_LLM_PROVIDER=anthropic` (`claude-sonnet-5-5`, full build: 84 LLM
calls during the build, 0 failed, all 11 SOPs published):

| Suite | Passed | Metrics |
| --- | --- | --- |
| ask | 22/24 | correct refusals 100%, correct answers 78%, **unsafe answers 0** |
| project_check | 19/20 | catch rate 100%, false alarms 12% |
| review | 26/26 | catch rate 100%, false alarms 0% |
| triage | 12/12 | incident recall 100%, grouping 100% |

The three failures were over-cautious, not unsafe: Claude refused two answerable questions
because retrieval handed it duplicate lines and missed the manual's lens-cleaning line and the
soldering PPE lines ("soldering" didn't match "solder"); and Claude listed "circuit board" and
"header pins" as materials, so a safe soldering project needed staff. Fixed afterwards
(dedup and wider retrieval, light stemming, parts aren't unknown materials on component
machines, clearer prompt), with regression tests built from Claude's real replies. Offline
suites still pass 100%. **Rerun with Claude to get post-fix numbers** before the report, and keep
this table as the "before" row.

## Red-team additions (2026-11-11)

Twelve `rt-*` cases were added to `sets/ask.json` after probing Ask by hand with about 50
hostile or odd questions. The probe found: emergencies with no matching word ("I got
electrocuted", "somebody fainted", "there is smoke in the room") got a plain refusal instead of
emergency info; high-risk requests ("get around the interlock", "override the e-stop", "go get
lunch" while the laser runs) were answered or sent to the wrong code; and a "Yes" for a
material dropped the manual's limit ("up to 6 mm"). Review also crashed on malformed drafts
and accepted a changed unit ("6 mm" -> "6 cm"). All were fixed with regression tests in
`tests/agents/test_ask_hardening.py`, `tests/agents/test_review_hardening.py` and
`tests/core/test_escalation_catalog.py`. **These cases came from the probe, so like the
others they are no longer a held-out test.**
