"""Regressions from the first full run with Claude on 2026-11-18 (Lluc's Mac).

The offline fallback always says "answerable", so these only showed with a real model:
* "What PPE do I need for the laser cutter?" was refused. The two-word overlap filter counted the
  machine's name ("laser") and filler ("need"), so the unattended-use line passed and every
  safety-glasses line was dropped before Claude saw them. Only subject words count now.
* "How do I clean the lens on the laser cutter?" was refused because the documents only partly
  answer it; Ask now gives the supported part and says what is missing.
* Refusals lost their trace, so the cause could not be seen.
The Claude replies below are the ones recorded on that run.
"""

from msa.agents.ask.agent import PARTIAL_NOTE, content_tokens
from msa.core.llm import ScriptedLLM, set_llm
from tests.agents.test_ask import ask, shop  # noqa: F401  (shop is a fixture)


def capture(reply):
    seen = {}

    def answer(prompt):
        seen["prompt"] = prompt
        return reply

    set_llm(ScriptedLLM({"ask.answer": answer}))
    return seen


def test_machine_name_words_do_not_count_as_a_match(db, shop):  # noqa: F811
    eq, _, _ = shop
    assert content_tokens("What PPE do I need for the laser cutter?", eq) == {"ppe"}
    assert content_tokens("Tell me about the Lumenic Vector 60", eq)  # never empty


def test_ppe_lines_reach_claude(db, shop):  # noqa: F811
    _, trained, _ = shop
    seen = capture({"answerable": True, "partial": False, "used": [0],
                    "answer": "Wear safety glasses when you clean the lens or mirrors."})
    r = ask(db, trained, "What PPE do I need for the laser cutter?")
    assert r.status == "ok" and r.citations
    assert "safety glasses" in seen["prompt"].lower()


def test_partial_answer_is_given_with_a_note(db, shop):  # noqa: F811
    _, trained, _ = shop
    capture({"answerable": True, "partial": True, "used": [0, 1],
             "answer": "Clean the focus lens with lens wipes every 8 hours of use and wear safety "
                       "glasses. The documents don't give step-by-step instructions."})
    r = ask(db, trained, "How do I clean the lens on the laser cutter?")
    assert r.status == "ok" and r.citations and r.output["partial"] is True
    assert r.output["answer"].endswith(PARTIAL_NOTE)
    assert not r.escalations


def test_refusal_keeps_its_trace(db, shop):  # noqa: F811
    _, trained, _ = shop
    capture({"answerable": False, "used": [0],
             "answer": "The passages don't say what PPE is needed for the laser cutter."})
    r = ask(db, trained, "What PPE do I need for the laser cutter?")
    assert r.status == "refused" and r.escalations == ["E03"]
    assert any("answerable=False" in t for t in r.trace)


def test_off_topic_is_still_refused(db, shop):  # noqa: F811
    _, trained, _ = shop
    r = ask(db, trained, "What is the capital of France?")
    assert r.status == "refused" and r.escalations == ["E03"]


def test_one_stray_word_is_not_a_match(db, shop):  # noqa: F811
    """A looser one-word filter (tried first) let "Who won the game last night?" through."""
    _, trained, _ = shop
    for q in ("Who won the game last night?", "What time does the shop close?",
              "DROP TABLE users; --"):
        assert ask(db, trained, q).escalations == ["E03"], q
