import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fixtures import make_run, node  # noqa: E402
from rsi import harness as hz  # noqa: E402
from rsi.backend import run_env  # noqa: E402
from rsi.improver import Improver  # noqa: E402
from rsi.llm import ScriptedLLM  # noqa: E402
from rsi.loop import Loop, LoopError  # noqa: E402
from rsi.task import load_task  # noqa: E402

ROAP = load_task("random_acts_of_pizza")
INSULTS = load_task("insults")
NOTES = "Drop columns that are missing from the test set; do not fill them with defaults.\n"


def edit_turns(notes=NOTES):
    return [[("read_file", {"path": "context/memory.jsonl"})],
            [("write_file", {"path": "harness/notes.md", "content": notes})],
            [("check", {})],
            [("finish", {"summary": "added a note on train-only fields (train_only_field = -1)"})]]


def test_full_round_and_next_harness_delivered(tmp_path):
    loop = Loop(tmp_path / "state")
    loop.init()
    run0 = make_run(tmp_path / "r0", [node(0, val=0.6)])
    rec = loop.collect(ROAP, 0, run0)
    assert rec["vector"]["official_score"] == 0.6 and len(loop.memory.records()) == 1

    llm = ScriptedLLM(edit_turns())
    out = loop.improve(ROAP, 0, Improver(llm))
    assert out["outcome"] == "edited" and "+Drop columns" in out["diff"]
    assert hz.read(loop.harness_dir(1))["notes.md"] == NOTES
    assert "official_score" in llm.seen[0][1]["content"]           # memory summary in the first prompt

    # round 1 must have received H1: wrong variant -> refused, right one -> accepted, change note recorded
    bad = make_run(tmp_path / "r1bad", [node(0)])
    with pytest.raises(LoopError, match="did not reach"):
        loop.collect(ROAP, 1, bad)
    good = make_run(tmp_path / "r1", [node(0)], variant=hz.notes_variant(NOTES), notes="header\n" + NOTES)
    rec1 = loop.collect(ROAP, 1, good)
    assert rec1["change"].startswith("added a note")


def test_no_change_copies_harness(tmp_path):
    loop = Loop(tmp_path / "s")
    loop.init()
    loop.collect(ROAP, 0, make_run(tmp_path / "r0", [node(0)]))
    out = loop.improve(ROAP, 0, Improver(ScriptedLLM([[("finish", {"summary": "no evidence for a change"})]])))
    assert out["outcome"] == "no_change" and hz.read(loop.harness_dir(1)) == hz.read(loop.harness_dir(0))


def test_improver_cannot_write_outside_harness_or_finish_invalid(tmp_path):
    loop = Loop(tmp_path / "s")
    loop.init()
    loop.collect(ROAP, 0, make_run(tmp_path / "r0", [node(0)]))
    llm = ScriptedLLM([[("write_file", {"path": "context/memory.jsonl", "content": "x"})],
                       [("write_file", {"path": "harness/config.json", "content": '{"tree_topk": 99}'})],
                       [("finish", {"summary": "s"})]])
    out = loop.improve(ROAP, 0, Improver(llm, max_steps=3))
    assert out["outcome"] == "unfinished"                            # finish refused while config is invalid
    assert hz.read(loop.harness_dir(1)) == hz.read(loop.harness_dir(0))


def test_test_task_never_reaches_memory_or_improve(tmp_path):
    loop = Loop(tmp_path / "s")
    loop.init()
    rec = loop.collect(INSULTS, 0, make_run(tmp_path / "r0", [node(0)], score=0.9))
    assert rec["vector"]["official_score"] is None and loop.memory.records() == []
    with pytest.raises(LoopError, match="test task"):
        loop.improve(INSULTS, 0, Improver(ScriptedLLM([])))


def test_validate_and_env():
    files = hz.read(Path(__file__).resolve().parents[1] / "harness" / "H0")
    assert hz.validate(files) == []
    env = run_env(ROAP, files)
    assert env["AIDE_SELECTION_MODE"] == "agent" and env["AIDE_TEST_FEEDBACK"] == "0"
    assert "PROMPT_VARIANT" not in env and "AIDE_SUB_STATS" not in env
    cfg = json.loads(files["config.json"])
    cfg["submission_profile"] = True
    env = run_env(ROAP, {"notes.md": NOTES, "config.json": json.dumps(cfg)})
    assert env["AIDE_SUB_STATS"] == "1" and env["PROMPT_VARIANT"] == hz.notes_variant(NOTES)
    assert hz.validate({"notes.md": "", "config.json": '{"tree_topk": 5}', "x.py": ""})
