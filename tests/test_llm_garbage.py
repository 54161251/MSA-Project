"""Every agent must survive a model that returns the wrong shapes (wrong types, strings for
lists, numbers for strings): fall back or skip, never crash, and still publish every SOP."""

import itertools

from msa.core.llm import LLM, set_llm

GARBAGE = [
    {"statements": {"PPE": "wear stuff"}, "steps": 5, "plans": "weekly", "questions": [1, 2],
     "supported": "false", "answerable": "yes", "used": "0", "kind": 3, "issue_type": None,
     "item_id": 7, "quantity": "two", "summary": 12, "specs": [], "footprint_m": "1 x 2"},
    {"statements": [None, 3, "Wear glasses.", {"text": ""}], "steps": ["cut it", None, {"text": 4}],
     "plans": [{"task": 5, "interval_days": "often"}], "questions": [{"prompt": 1}],
     "supported": None, "answerable": None, "used": [99, "x"], "kind": ["incident"]},
    {},
]


class GarbageLLM(LLM):
    provider = "garbage"

    def __init__(self):
        self.cycle = itertools.cycle(GARBAGE)

    def complete_json(self, task, system, prompt, fallback, max_tokens=1500):
        return dict(next(self.cycle))


def test_garbage_model_output_never_crashes(tmp_path, monkeypatch):
    from eval import run

    monkeypatch.setattr(run, "RESULTS", tmp_path)
    set_llm(GarbageLLM())
    try:
        build = run.build_makerspace()
        assert build["machines_without_approved_sop"] == []
        for name, mod in run.load_suites().items():
            cases = __import__("json").loads((run.SETS / f"{name}.json").read_text())
            mod.run_suite(cases)  # results don't matter here; not crashing does
    finally:
        set_llm(None)
