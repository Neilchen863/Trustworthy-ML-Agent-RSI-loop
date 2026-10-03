"""python -m rsi <command> ...   (see README)"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .loop import Loop
from .run import load_run
from .task import list_tasks, load_task
from .verifiers import verify_all


def _print(obj):
    print(json.dumps(obj, indent=2, ensure_ascii=False, default=str))


def main(argv=None):
    p = argparse.ArgumentParser(prog="rsi")
    p.add_argument("--state", default=".state", help="state directory (default .state)")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("tasks", help="list tasks")
    sub.add_parser("init", help="create H0 in the state directory")
    s = sub.add_parser("freeze", help="freeze the method (code, tests, task protocol, H0 template, overlay, "
                                      "improver model) for this state")
    s.add_argument("--model", required=True)
    s.add_argument("--overlay", help="execution-base overlay to record (path is hashed)")
    s = sub.add_parser("budget", help="show spending; --cap sets the total cap in USD")
    s.add_argument("--cap", type=float)
    s.add_argument("--extra", type=float, help="record spending not visible in rounds/ (USD)")
    s.add_argument("--note", default="")
    s.add_argument("--history", type=float, help="spending of earlier states, for the cumulative total (USD)")

    s = sub.add_parser("score", help="run the verifiers on any run directory (no state change)")
    s.add_argument("--task", required=True)
    s.add_argument("run_dir")

    s = sub.add_parser("submit", help="start AIDE on CRC with H<round>")
    s.add_argument("--task", required=True)
    s.add_argument("--round", type=int, required=True)
    s.add_argument("--dry-run", action="store_true")
    s.add_argument("--rep", type=int, default=1, help="replicate id; >1 = evaluation only, never in memory")
    s.add_argument("--aide-root")
    s.add_argument("--overlay")

    s = sub.add_parser("collect", help="verify a finished run; train tasks also write memory")
    s.add_argument("--task", required=True)
    s.add_argument("--round", type=int, required=True)
    s.add_argument("run_dir")
    s.add_argument("--no-delivery-check", action="store_true", help="skip the delivery/receipt check (recorded)")
    s.add_argument("--reused", help="note on where an older run started outside this state comes from; "
                                    "implies no delivery check, recorded as provenance")
    s.add_argument("--rep", type=int, default=1)

    s = sub.add_parser("improve", help="LLM edits H<round> into H<round+1>")
    s.add_argument("--task", required=True)
    s.add_argument("--round", type=int, required=True)
    s.add_argument("--model", help="default: the frozen improver model")
    s.add_argument("--max-cost", type=float, default=0.5)

    a = p.parse_args(argv)
    loop = Loop(Path(a.state))
    if a.cmd == "tasks":
        for name in list_tasks():
            t = load_task(name)
            print(f"{name:24s} {t.role:5s} {t.competition_id}")
    elif a.cmd == "init":
        print(loop.init())
    elif a.cmd == "freeze":
        from .method import freeze
        d = freeze(Path(a.state), a.model, a.overlay)
        _print({k: v for k, v in d.items() if k != "files"})
    elif a.cmd == "budget":
        from .budget import add_extra, ledger, set_cap, set_history
        if a.cap is not None:
            set_cap(Path(a.state), a.cap)
        if a.history is not None:
            set_history(Path(a.state), a.history, a.note)
        if a.extra is not None:
            add_extra(Path(a.state), a.extra, a.note)
        _print(ledger(Path(a.state)))
    elif a.cmd == "score":
        r = verify_all(load_run(a.run_dir), load_task(a.task))
        _print(r)
    elif a.cmd == "submit":
        from .backend import SgeBackend
        from .method import frozen
        overlay = a.overlay or ((frozen(Path(a.state)) or {}).get("overlay") or {}).get("path")
        _print(loop.submit(load_task(a.task), a.round, SgeBackend(a.aide_root, overlay), dry_run=a.dry_run,
                          rep=a.rep))
    elif a.cmd == "collect":
        rec = loop.collect(load_task(a.task), a.round, a.run_dir, check_delivery=not a.no_delivery_check, rep=a.rep,
                           reused=a.reused)
        _print({"round": rec["round"], "harness": rec["harness"], "vector": rec["vector"],
                "dynamics": rec["dynamics"]["summary"], "delivery": rec["delivery"]})
    elif a.cmd == "improve":
        from .improver import Improver
        from .llm import OpenAIChat
        from .budget import improver_allowance
        from .method import frozen
        model = a.model or (frozen(Path(a.state)) or {}).get("improver_model")
        if not model:
            raise SystemExit("no --model and the state is not frozen (rsi freeze --model ...)")
        cap = improver_allowance(Path(a.state), a.max_cost)
        out = loop.improve(load_task(a.task), a.round, Improver(OpenAIChat(model, max_cost_usd=cap)))
        _print({k: out[k] for k in ("from", "to", "outcome", "published", "candidate", "proposal", "reason",
                                    "controller_check", "cost_usd")})
        print(out["diff"] or "(no diff)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
