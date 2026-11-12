"""Quiz bank generation and attempts.

Questions come only from APPROVED documents (G4), and every question carries the citation
of the statement it tests. A bank is a draft until staff approve it (quiz_bank approval).

Attempts: draw `draw_count` questions at random, pass mark 80% by default, retake 24 hours
after a failed attempt. Passing moves training to quiz_passed, then to certified (approval
mode `auto`) or pending_check plus a competency_check approval (mode `competency_check`).
"""

from __future__ import annotations

import hashlib
import random
from datetime import timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from msa.agents.intake.drafting import rule_materials, rule_verdict
from msa.core import training
from msa.core.contracts import AgentResult
from msa.core.db import new_id, utcnow
from msa.core.docs import approved_docs
from msa.core.llm import get_llm
from msa.core.models import Equipment, QuizAttempt, QuizBank, User
from msa.core.registry import registry
from msa.core.services import ServiceError, request_approval

MAX_QUESTIONS = 15
RETAKE_HOURS = 24

WRONG = {
    "ppe": ["No safety gear is needed for short jobs.",
            "Gloves are required on every machine in the shop, whatever the task.",
            "Regular prescription glasses are enough eye protection for any job."],
    "warning": ["It's fine to leave the machine running if you stay in the building.",
                "Guards and interlocks can be removed if they slow the job down.",
                "Small problems don't need to be reported to staff."],
    "step": ["Start the machine first, then check your material is approved.",
             "Skip the test run if you have used a similar machine before.",
             "Leave cleanup for the next person."],
    "emergency": ["Keep working and report it at the end of the day.",
                  "Open the machine right away to see what happened.",
                  "Leave the shop without telling anyone."],
}


def _shuffle(options: list[str], key: str) -> tuple[list[str], int]:
    rng = random.Random(int(hashlib.sha256(key.encode()).hexdigest()[:8], 16))
    idx = list(range(len(options)))
    rng.shuffle(idx)
    return [options[i] for i in idx], idx.index(0)


def deterministic_questions(eq: Equipment, statements: list[dict[str, Any]],
                            guidelines: str = "") -> list[dict[str, Any]]:
    qs: list[dict[str, Any]] = []
    name = eq.name.split(" (")[0]

    def add(prompt, correct, wrong, st, kind):
        if len(wrong) < 2:
            return
        opts, ans = _shuffle([correct, *wrong[:3]], f"{eq.id}:{prompt}:{correct}")
        qs.append({"id": hashlib.sha1(f"{eq.id}|{prompt}|{correct}".encode()).hexdigest()[:12],
                   "prompt": prompt, "options": opts, "answer_index": ans, "kind": kind,
                   "citations": st.get("citations", []) if st else []})

    verdicts: dict[str, set[str]] = {}
    first: dict[str, dict] = {}
    for st in statements:
        if st.get("kind") == "material" and st.get("citations"):
            v = rule_verdict(st["text"])
            for m in rule_materials(st["text"]):
                verdicts.setdefault(m, set()).add(v or "")
                first.setdefault(m, st)
    # a material the list treats two ways ("Allowed: standard resin" / "Never: resin you brought
    # from home") has no single right answer, so it isn't asked about
    allowed = [(m.replace("_", " "), first[m]) for m, v in verdicts.items() if v == {"allowed"}]
    banned = [(m.replace("_", " "), first[m]) for m, v in verdicts.items() if v == {"banned"}]
    for mat, st in banned:
        wrong = [a for a, _ in allowed if a != mat]
        add(f"Which material must never be used on the {name}?", mat, wrong, st, "material")
    for mat, st in allowed[:3]:
        wrong = [b for b, _ in banned if b != mat]
        add(f"Which of these materials is approved for the {name}?", mat, wrong, st, "material")
    prompts = {
        "ppe": f"Which of these is a safety rule for the {name}?",
        "warning": f"Which statement about the {name} is correct?",
        "emergency": f"What does the SOP say to do in an emergency at the {name}?",
        "step": f"Which of these is part of the procedure for the {name}?",
    }
    for st in statements:
        k = st.get("kind")
        if k in prompts and st.get("citations"):
            add(prompts[k], st["text"], WRONG[k], st, k)
    # one question per correct answer text
    seen, out = set(), []
    for q in qs:
        key = q["options"][q["answer_index"]]
        if key not in seen:
            seen.add(key)
            out.append(q)
    return balanced(out, guidelines)


# Bank order: every kind gets a turn, materials twice per round (they drive the material check).
# Without this a laser bank filled up with material and PPE questions and had none on
# operation or emergencies.
KIND_CYCLE = ["material", "ppe", "warning", "material", "step", "emergency"]


def balanced(questions: list[dict], guidelines: str = "", limit: int = MAX_QUESTIONS) -> list[dict]:
    queues: dict[str, list[dict]] = {}
    for q in questions:
        queues.setdefault(q.get("kind") or "other", []).append(q)
    out: list[dict] = []
    if "material" in guidelines.lower():  # staff asked for a material focus
        out += queues.pop("material", [])[:limit]
    cycle = KIND_CYCLE + sorted(k for k in queues if k not in KIND_CYCLE)
    while len(out) < limit and any(queues.values()):
        for kind in cycle:
            if queues.get(kind) and len(out) < limit:
                out.append(queues[kind].pop(0))
    return out[:limit]


SYSTEM = (
    "Write multiple-choice quiz questions that test the given SOP statements. Each question tests "
    "exactly one statement (give its index as source_index). Exactly one option is correct and it "
    "must say what the statement says. Wrong options must be clearly wrong for this machine. "
    'Return {"questions": [{"prompt", "options", "answer_index", "source_index"}]}'
)


@registry.agent("quiz")
def generate(db: Session, payload: dict, actor: str) -> AgentResult:
    eq = db.get(Equipment, payload.get("equipment_id", ""))
    if eq is None:
        return AgentResult(agent="quiz", status="error", output={"error": "unknown equipment"})
    docs = approved_docs(db, eq.id, ["sop", "materials"])
    if not any(d.type == "sop" for d in docs):
        return AgentResult(agent="quiz", status="refused", escalations=["E02"],
                           output={"message": "No approved SOP yet; quizzes are built from it."})
    statements = [s for d in docs for s in d.statements]
    guidelines = payload.get("guidelines", "")
    fallback = {"questions": deterministic_questions(eq, statements, guidelines)}
    numbered = "\n".join(f"{i}. [{s['kind']}] {s['text']}" for i, s in enumerate(statements))
    out = get_llm().complete_json(
        "quiz.generate", SYSTEM,
        f"Machine: {eq.name}\nStaff guidelines: {guidelines or 'none'}\n\nStatements:\n{numbered}",
        lambda: fallback, max_tokens=8000)
    questions = _validate(out.get("questions", []), statements, guidelines) \
        if out is not fallback else fallback["questions"]
    if not questions:
        questions = fallback["questions"]
    prev = db.scalar(select(QuizBank).where(QuizBank.equipment_id == eq.id)
                     .order_by(QuizBank.version.desc()).limit(1))
    bank = QuizBank(equipment_id=eq.id, version=(prev.version + 1) if prev else 1,
                    questions=questions, guidelines=guidelines, created_by="quiz",
                    draw_count=min(10, len(questions)))
    db.add(bank)
    db.flush()
    a = request_approval(db, "quiz_bank", bank.id, {"equipment_id": eq.id}, "quiz",
                         f"Quiz bank for {eq.name}: {len(questions)} questions")
    return AgentResult(agent="quiz", status="needs_approval", approvals=[a.id],
                       output={"bank_id": bank.id, "questions": len(questions)},
                       trace=[f"llm:quiz.generate -> {len(questions)} questions"])


def _validate(raw: list[dict], statements: list[dict], guidelines: str = "") -> list[dict]:
    out = []
    if not isinstance(raw, list):
        return out
    for q in raw:
        try:
            i = int(q["source_index"])
            if not 0 <= i < len(statements):  # a negative index would silently pick another line
                continue
            src = statements[i]
            opts = [" ".join(str(o).split()) for o in q["options"]][:6]
            ans = int(q["answer_index"])
            prompt = " ".join(str(q["prompt"]).split())
            if not prompt or len(opts) < 2 or not 0 <= ans < len(opts) or not src.get("citations"):
                continue
            if any(not o for o in opts) or len({o.lower() for o in opts}) != len(opts):
                continue  # blank or repeated options make the answer ambiguous
            out.append({"id": new_id()[:12], "prompt": prompt, "options": opts,
                        "answer_index": ans, "kind": src.get("kind"),
                        "citations": src["citations"]})
        except (KeyError, ValueError, IndexError, TypeError, AttributeError):
            continue
    return balanced(out, guidelines)


# ------------------------------------------------------------------ attempts


def approved_bank(db: Session, equipment_id: str) -> QuizBank | None:
    return db.scalar(select(QuizBank).where(QuizBank.equipment_id == equipment_id,
                                            QuizBank.status == "approved")
                     .order_by(QuizBank.version.desc()).limit(1))


def start_attempt(db: Session, user: User, equipment_id: str) -> tuple[QuizAttempt, list[dict]]:
    bank = approved_bank(db, equipment_id)
    if bank is None:
        raise ServiceError("This machine has no approved quiz yet")
    state = training.current_state(db, user.id, equipment_id)
    if state == "revoked":
        raise ServiceError("Your training on this machine was revoked; please talk to staff")
    if state in ("pending_check", "certified"):
        raise ServiceError(f"Your training is already {state.replace('_', ' ')}")
    last_fail = db.scalar(select(QuizAttempt).where(
        QuizAttempt.user_id == user.id, QuizAttempt.equipment_id == equipment_id,
        QuizAttempt.passed.is_(False)).order_by(QuizAttempt.submitted_at.desc()).limit(1))
    if last_fail and last_fail.submitted_at and \
            utcnow() - last_fail.submitted_at < timedelta(hours=RETAKE_HOURS):
        wait = timedelta(hours=RETAKE_HOURS) - (utcnow() - last_fail.submitted_at)
        raise ServiceError(f"You can retake this quiz in {int(wait.total_seconds() // 3600) + 1} hours")
    by_id = {q["id"]: q for q in bank.questions}
    # An open attempt is resumed, not redrawn: otherwise starting again and again shows the
    # whole bank before submitting, and abandoning an attempt sidesteps the retake wait.
    open_attempt = db.scalar(select(QuizAttempt).where(
        QuizAttempt.user_id == user.id, QuizAttempt.equipment_id == equipment_id,
        QuizAttempt.bank_id == bank.id, QuizAttempt.submitted_at.is_(None))
        .order_by(QuizAttempt.created_at.desc()).limit(1))
    if open_attempt is not None and all(q in by_id for q in open_attempt.question_ids or []) \
            and open_attempt.question_ids:
        chosen = [by_id[q] for q in open_attempt.question_ids]
        return open_attempt, _public(chosen)
    attempt = QuizAttempt(user_id=user.id, equipment_id=equipment_id, bank_id=bank.id,
                          created_by=user.id)
    chosen = draw(bank.questions, bank.draw_count or 10, random.Random(new_id()))
    attempt.question_ids = [q["id"] for q in chosen]
    db.add(attempt)
    db.flush()
    return attempt, _public(chosen)


def _public(chosen: list[dict]) -> list[dict]:
    return [{"id": q["id"], "prompt": q["prompt"], "options": q["options"]} for q in chosen]


def draw(questions: list[dict], count: int, rng: random.Random) -> list[dict]:
    """Random draw that covers every kind in the bank when there is room (one per kind first,
    then the rest at random), so an attempt can't skip emergencies or PPE by chance."""
    n = min(count, len(questions))
    kinds: dict[str, list[dict]] = {}
    for q in questions:
        kinds.setdefault(q.get("kind") or "other", []).append(q)
    picked = [rng.choice(qs) for qs in kinds.values()] if len(kinds) <= n else []
    rest = [q for q in questions if q not in picked]
    picked += rng.sample(rest, n - len(picked))
    rng.shuffle(picked)
    return picked


def submit_attempt(db: Session, user: User, attempt_id: str, answers: dict[str, int]) -> dict:
    a = db.get(QuizAttempt, attempt_id)
    if a is None or a.user_id != user.id:
        raise ServiceError("No such quiz attempt")
    if a.submitted_at is not None:
        raise ServiceError("This attempt was already submitted")
    bank = db.get(QuizBank, a.bank_id)
    qs = {q["id"]: q for q in bank.questions}
    correct = sum(1 for qid in a.question_ids if answers.get(qid) == qs[qid]["answer_index"])
    score = correct / max(1, len(a.question_ids))
    a.answers = {k: int(v) for k, v in answers.items()}
    a.score = round(score, 3)
    a.passed = score >= bank.pass_mark
    a.submitted_at = utcnow()
    eq = db.get(Equipment, a.equipment_id)
    result = {"score": a.score, "passed": a.passed, "pass_mark": bank.pass_mark,
              "review": [{"prompt": qs[qid]["prompt"],
                          "your_answer": answers.get(qid),
                          "correct": answers.get(qid) == qs[qid]["answer_index"],
                          "citations": qs[qid].get("citations", [])} for qid in a.question_ids]}
    if not a.passed:
        result["next"] = f"Review the training guide; you can retake in {RETAKE_HOURS} hours."
        return result
    training.set_state(db, user.id, eq.id, "quiz_passed", "quiz", quiz_score=a.score)
    if eq.approval_mode == "auto":
        training.set_state(db, user.id, eq.id, "certified", "quiz:auto",
                           reason="auto approval on passing the quiz")
        result["next"] = f"You're certified on the {eq.name}."
    else:
        training.set_state(db, user.id, eq.id, "pending_check", "quiz")
        request_approval(db, "competency_check", f"{user.id}:{eq.id}",
                         {"user_id": user.id, "equipment_id": eq.id, "score": a.score}, "quiz",
                         f"In-person check needed: member {user.id[:6]} on {eq.name}")
        result["next"] = "Passed. A staff member will do a short in-person check before you can use it."
    return result
