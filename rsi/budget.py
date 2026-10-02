"""Total spending cap for one state directory.

    spent = finished runs (TOTAL ESTIMATED COST in each run's cost.txt, recorded at collect)
          + runs submitted but not collected yet (RESERVE_PER_RUN each)
          + every improver session (cost_usd in improve.json)

submit refuses when spent + RESERVE_PER_RUN would exceed the cap; improve gets at most what is left.
Costs are list-price estimates from logged tokens, not the OpenAI invoice."""
from __future__ import annotations

import json
import re
from pathlib import Path

RESERVE_PER_RUN = 3.0          # worst case for a 25-step, 2h gpt-4o run (observed $1.2-1.6)


class BudgetError(RuntimeError):
    pass


def run_cost(run_dir) -> float | None:
    f = Path(run_dir) / "cost.txt"
    if not f.is_file():
        return None
    m = re.search(r"TOTAL ESTIMATED COST:\s*\$([\d.]+)", f.read_text(errors="ignore"))
    return float(m.group(1)) if m else None


def ledger(state: Path) -> dict:
    state = Path(state)
    cfg = state / "budget.json"
    conf = json.loads(cfg.read_text()) if cfg.is_file() else {}
    cap = conf.get("cap_usd")
    items = [{"what": e["what"], "usd": e["usd"], "kind": "extra"} for e in conf.get("extra", [])]
    for sub in sorted(state.glob("rounds/*/round_*/submit.json")):
        d = sub.parent
        s = json.loads(sub.read_text())
        rew = d / "reward.json"
        if rew.is_file():
            cost = json.loads(rew.read_text()).get("run_cost_usd")
            items.append({"what": f"run {d.parent.name}/{d.name} job {s.get('job_id')}",
                          "usd": RESERVE_PER_RUN if cost is None else cost,
                          "kind": "reserved (no cost.txt)" if cost is None else "run"})
        else:
            items.append({"what": f"run {d.parent.name}/{d.name} job {s.get('job_id')}", "usd": RESERVE_PER_RUN,
                          "kind": "reserved (not collected)"})
    for imp in sorted(state.glob("rounds/*/round_*/improve.json")):
        items.append({"what": f"improve {imp.parent.parent.name}/{imp.parent.name}",
                      "usd": json.loads(imp.read_text()).get("cost_usd") or 0.0, "kind": "improver"})
    spent = round(sum(i["usd"] for i in items), 4)
    return {"cap_usd": cap, "spent_usd": spent, "left_usd": None if cap is None else round(cap - spent, 4),
            "items": items}


def check_submit(state: Path) -> dict:
    led = ledger(state)
    if led["cap_usd"] is None:
        raise BudgetError("no budget set: run `python -m rsi budget --cap <usd>` first")
    if led["spent_usd"] + RESERVE_PER_RUN > led["cap_usd"]:
        raise BudgetError(f"budget: spent ${led['spent_usd']:.2f} + ${RESERVE_PER_RUN:.2f} reserve for a new run "
                          f"> cap ${led['cap_usd']:.2f}")
    return led


def improver_allowance(state: Path, requested: float) -> float:
    led = ledger(state)
    if led["cap_usd"] is None:
        raise BudgetError("no budget set: run `python -m rsi budget --cap <usd>` first")
    left = led["left_usd"]
    if left <= 0:
        raise BudgetError(f"budget exhausted: spent ${led['spent_usd']:.2f} of ${led['cap_usd']:.2f}")
    return min(requested, left)


def set_cap(state: Path, cap: float) -> None:
    _update(state, lambda c: c.update(cap_usd=cap))


def add_extra(state: Path, usd: float, what: str) -> None:
    """Spending the rounds/ files do not show (e.g. superseded improver sessions)."""
    _update(state, lambda c: c.setdefault("extra", []).append({"what": what, "usd": usd}))


def _update(state: Path, change) -> None:
    f = Path(state) / "budget.json"
    Path(state).mkdir(parents=True, exist_ok=True)
    conf = json.loads(f.read_text()) if f.is_file() else {}
    change(conf)
    f.write_text(json.dumps(conf, indent=1) + "\n")
