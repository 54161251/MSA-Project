# R5 Review, Q&A and evaluation (Lluc)

**Branch:** `r5/review-ask-eval` (after R4)

## What's in it
* `src/msa/agents/review/agent.py`: runs on every Intake draft. Per statement: E07 (no citation,
  broken citation, or the source doesn't support it, including changed numbers, flipped
  negation and added qualifiers like "optional"), E08 (contradicts a staff note), E09 (banned
  material listed as allowed). Per SOP: E09 for missing sections or PPE terms. Fixable issues
  go back to Intake for up to 3 revise cycles; anything left blocks publishing and escalates.
* `src/msa/agents/ask/agent.py`: emergency words first (E10, emergency info, no LLM), then high-risk
  requests (E04), material yes/no via the rules tool, a training check for how-to questions
  (E05/E06/E13), retrieval from approved documents only, and refusal when nothing supports an
  answer (E03).
* `eval/`: four suites with expected results written before running; see `eval/README.md`,
  including the honest history of what the first run found.

## Demo
Ask as "priya": *Can I cut PVC on the laser cutter?*, *What should I check before starting the
laser?*, *How do I bypass the lid interlock?*, *There's smoke coming out of the laser!* Then
show the Approvals page: the laser SOP's review summary lists the polycarbonate line it removed.
Run `python -m eval.run`.

## Known limits
After fixing what the first eval run found, the offline suites pass 100 %, so they're no longer
a held-out test: write fresh cases before the final report and also run with the real LLM.
