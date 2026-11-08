"""Runs every evaluation suite in CI so a regression shows up as a failing test."""

import json

from eval import run


def test_eval_suites_run_and_have_no_unsafe_answers(tmp_path, monkeypatch):
    monkeypatch.setattr(run, "RESULTS", tmp_path)
    assert run.main([]) == 0
    report = json.loads((tmp_path / "latest.json").read_text())
    assert report["suites"], "no suites ran"
    if "ask" in report["suites"]:
        assert report["suites"]["ask"]["metrics"]["unsafe_answers"] == 0
