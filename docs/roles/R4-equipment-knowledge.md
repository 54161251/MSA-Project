# R4 Equipment knowledge (Jad)

**Branch:** `r4/equipment-knowledge` (after R3)

## What's in it
* `data/makerspace/`: the fictional makerspace: settings, 11 machines (`equipment.yaml`),
  fake members, and 10 invented manuals in `manuals/` with `[p.N]` page markers. Two manuals
  contain deliberate problems: the laser manual lists polycarbonate as allowed (Review must
  remove it) and the sewing manual has no footprint (Intake raises E12).
* `src/msa/agents/intake/`: finds the manual (library lookup by brand and model, or a staff
  upload incl. PDF), extracts specs with confidence (E12 when the footprint is unclear), and
  drafts the SOP, training guide and materials list. Every statement cites the manual page and
  section. Staff notes become approved sources, and their lines go into the drafts word for word
  even under a heading like "House rules" (material lines to Material rules, PPE and emergency
  lines to their sections, anything else to Warnings). Publishing a materials list builds the
  machine's material rules:
  * only the line's subject counts: "Never: metal with the wood blade" bans the metals, not wood;
    a generic "metal" covers aluminum, steel, brass and copper;
  * staff house rules come first, so they win over the manual;
  * otherwise the first line about a material sets the rule, and a later line that disagrees is
    added to the rule's reason as "Exception: ..." (resin printer: shop resin allowed, resin from
    home not);
  * lines with a verdict but no known material ("Never: soldering to lithium battery cells")
    can't be checked by the tools; they're listed in the `material_rules.rebuilt` audit entry.
* `src/msa/agents/quiz/`: builds up to 15 questions from approved docs only, each citing its source.
  The bank rotates through question kinds (materials twice per round, then PPE, warnings, steps,
  emergencies) so no machine's quiz is all material questions; materials the list treats two ways
  aren't asked about. Each attempt draws 10 with at least one of each kind, 80 % pass mark, 24 h
  retake wait. Starting again resumes the open attempt (same questions) instead of drawing new
  ones. Passing certifies (auto mode) or queues an in-person competency check (staff approval).
  A quiz bank is drafted automatically once a machine has an approved SOP and materials list.

Input checks: new machine ids are made URL-safe ("Laser Cutter #2" -> `laser-cutter-2`),
`approval_mode` must be `auto` or `competency_check`, and empty, damaged, scanned (no text) or
non-text uploads get a 400 with a reason. Manual lookup matches whole name parts only
("Kestrel 10" no longer finds the TS10 manual). Spec values from the model are sanity-checked:
confidences like "high" or "90%" are read as numbers, and a footprint outside 0.05–10 m is treated
as unread (E12).

## Demo
Staff: Manage equipment, add a machine by brand and model (or upload a manual), then show the
drafts in Approvals with their sources. Member "marcus": take the drill press quiz (auto
certification) or the laser quiz (competency check).

## Known limits
The manual "lookup" searches the local library folder, not the web. PDF parsing is best
effort (headings guessed from short lines). Offline quiz distractors are generic, so the correct
answer to a non-material question is often the longest, most specific option. Material rules
only know the materials in the safety tables (`safety_tools/rules/global.yaml`); anything else
falls back to "unknown, ask staff" in the material check.
