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
  section. Staff notes become approved sources. Publishing a materials list builds the
  machine's material rules.
* `src/msa/agents/quiz/`: builds up to 15 questions from approved docs only, each citing its source;
  draws 10 per attempt, 80 % pass mark, 24 h retake wait. Passing certifies (auto mode) or
  queues an in-person competency check (staff approval). A quiz bank is drafted automatically
  once a machine has an approved SOP and materials list.

## Demo
Staff: Manage equipment, add a machine by brand and model (or upload a manual), then show the
drafts in Approvals with their sources. Member "marcus": take the drill press quiz (auto
certification) or the laser quiz (competency check).

## Known limits
The manual "lookup" searches the local library folder, not the web. PDF parsing is best
effort (headings guessed from short lines). Offline quiz distractors are generic.
