import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fixtures import make_harness_run, make_run, node  # noqa: E402
from rsi import budget  # noqa: E402
from rsi.loop import Loop  # noqa: E402
from rsi.task import load_task  # noqa: E402

ROAP = load_task("random_acts_of_pizza")


class FakeBackend:
    overlay = None

    def submit(self, task, files, version_dir=None, dry_run=False):
        return {"dry_run": dry_run, "job_id": "1"}


def test_cap_reserve_and_actual_cost(tmp_path):
    loop = Loop(tmp_path / "s")
    loop.init()
    with pytest.raises(budget.BudgetError, match="no budget"):
        loop.submit(ROAP, 0, FakeBackend())
    budget.set_cap(loop.state, 5.0)
    loop.submit(ROAP, 0, FakeBackend())
    assert budget.ledger(loop.state)["spent_usd"] == budget.RESERVE_PER_RUN       # reserved while running
    run = make_harness_run(tmp_path / "r0", [node(0)], loop.harness_dir(0))
    (run / "cost.txt").write_text("TOTAL ESTIMATED COST: $1.4296 USD\n")
    loop.collect(ROAP, 0, run)
    assert budget.ledger(loop.state)["spent_usd"] == 1.4296                        # replaced by the real cost
    imp = loop.round_dir(ROAP, 0) / "improve.json"
    imp.write_text(json.dumps({"cost_usd": 0.6}))
    assert budget.improver_allowance(loop.state, 0.5) == 0.5
    from rsi import harness as hz
    hz.publish(loop.harness_dir(1), loop.harness(0), hz.read_manifest(loop.harness_dir(0)))
    with pytest.raises(budget.BudgetError, match="cap"):                          # 2.03 + 3.0 > 5
        loop.submit(ROAP, 1, FakeBackend())


def test_history_and_reused_runs(tmp_path):
    loop = Loop(tmp_path / "s")
    loop.init()
    budget.set_cap(loop.state, 15.0)
    budget.set_history(loop.state, 17.71, "rsi-loop .state + .state-accept")
    run = make_run(tmp_path / "old", [node(0)])
    (run / "cost.txt").write_text("TOTAL ESTIMATED COST: $1.47 USD\n")
    loop.collect(ROAP, 0, run, reused="old run")
    led = budget.ledger(loop.state)
    assert led["spent_usd"] == 0.0 and led["cumulative_usd"] == 17.71 and led["cumulative_cap_usd"] == 32.71
