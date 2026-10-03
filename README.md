# rsi-loop

```
fixed task, execution base, verifiers, budget
                │
  AIDE run with harness version H_t  ──>  verifiers + search dynamics + adapter receipt
                                                       │
                                       memory (summaries + evidence ids)
                                                       │
   improver LLM: evidence -> hypothesis -> edit of whitelisted harness files (candidate)
                                                       │
   fixed checks: boundary, isolated import, regression/replay, smoke through the real adapter
        ├─ fail: candidate kept under candidates/, H_{t+1} = copy of H_t
        └─ pass: immutable H_{t+1} published (manifest)  ──>  next AIDE run loads and executes it
```

## What the improver may change (the harness)

| path | reaches AIDE through | notes |
|---|---|---|
| `notes.md` | `PROMPT_VARIANT` notes file → every AIDE prompt | ≤ 8000 chars |
| `config.json` | env vars (`tree_topk`, `tree_recent` in agent mode; rule-mode keys; `submission_profile`) | keys of the other mode are frozen |
| `hooks/on_exec_error.py` | the fixed adapter in the dedicated v4 overlay calls `diagnose(event)` after a node's code raised; a returned string is appended to that node's execution output as `[harness note] ...`, which AIDE's reviewer and the debug step for that node read | stdlib allowlist (`re json math textwrap collections itertools functools string`), no file/exec/introspection, ≤ 2000-char output, isolated process, 10 s |

Nothing else: not the verifiers, tasks, data, budget, tests, adapter, candidate checks, collector, memory, the
improver itself, AIDE or the overlay. `manifest.json` in each version is written by the controller.

The entry point (`rsi/harness_adapter.py`, interface `on_exec_error/1`): input
`{exc_type, exc_message, traceback_tail, code}` of the failed node (no data, no labels); output `None` or a
string; failure (timeout, crash, invalid output) = nothing appended, recorded. No step, time or token budget is
added and the node stays failed. Every load and call is a line in `<run>/logs/harness_events.jsonl` with
SHA-256s of the loaded files and the adapter. H0's hook returns `None` (stock behaviour).

Isolation of the hook process: `python -I -S -B`, empty environment (no API key), fresh temporary working dir,
CPU and memory limits, `RLIMIT_FSIZE=0` (no bytes can be written to any file), wall-clock timeout; the source is
read and hash-checked once at load and executed from a private copy. It can still read files the AIDE process can
read and create empty files; the static rules forbid `open`, `os` and introspection for that reason.

## Layout

```
tasks/<task>/            task.json (competition, role train|test, metric, AIDE budget), instruction.md, prepare.sh
harness/H0/              starting version: notes.md (empty), config.json, hooks/on_exec_error.py (returns None)
rsi/                     frozen control and evaluation code
  harness.py             whitelist, static hook rules, digest, manifest, immutable publish, staging
  harness_adapter.py     the entry point inside AIDE (installed into the v4 overlay) + isolated hook runner
  candidate.py           candidate checks (boundary, interface, regression/replay, smoke)
  dynamics.py            search dynamics + evidence items with ids and sources
  improver.py            LLM tools: read files/evidence, write harness files, check (max 2), finish(proposal)
  loop.py, cli.py        init / freeze / budget / submit / collect / improve / score
  backend.py             SGE submission (preset RUN_ID, staged version), delivery check, harness receipt
  method.py              method freeze (explicit dependency list + overlay hash + improver model)
  run.py, memory.py, budget.py, task.py, llm.py, verifiers/
tests/                   offline: verifiers, loop, budget, entry point (isolation, faults, reproduced errors)
tools/                   build_overlay_v4.sh, acceptance_harness.py (A-E), acceptance.py (previous stage)
docs/                    plans, results, acceptance reports
```

## Verifiers (reward vector, unchanged in this stage)

`official_score` (train tasks only), `submission_sanity`, `train_only_field`, `implausible_validation`,
`preprocessing_outside_cv`, `metric_mismatch`, `selection`, `search_health`. All but `official_score` in [-1, 0],
0 = no problem found; `null` = not applicable. Each has a summary and evidence lines.

## Search dynamics and evidence

`collect` writes `dynamics.json` (per node: id, parent, step, status, metric (self-reported), exception type,
error signature, first-in-lineage vs suspected propagation, exec time, hook calls; run summary with coverage;
missing values are `null` + `missing_reason`) and `evidence.json` (items with stable ids such as
`j1500133:step07:error`, source pointer, observation, confidence observed/suspected/self_reported, bounded
excerpt, and for errors the exact hook input event). Memory keeps the summary and the evidence index; the
improver reads excerpts by id. Per-node tokens are `null` (`not_attributed`); training curves are not collected.

## Use (CRC)

```bash
python -m pytest -q tests
export MLEBENCH_AIDE_ROOT=~/mlebench-aide
qsub -q long -pe smp 1 -cwd -j y -o overlay_v4.log -S /bin/bash tools/build_overlay_v4.sh   # once
S=--state=.state-harness
python -m rsi $S freeze --model gpt-5.4 --overlay ~/rsi-loop-overlays/v4/agent_fixes_v4.overlay
python -m rsi $S init
python -m rsi $S budget --cap 15 --history 17.71 --note "earlier states"
python -m rsi $S collect --task random_acts_of_pizza --round 0 <old run dir> --reused "<where it comes from>"
python -m rsi $S improve --task random_acts_of_pizza --round 0 --max-cost 0.5     # -> candidate -> H1 or copy
python -m rsi $S submit  --task random_acts_of_pizza --round 1 [--dry-run]       # stages H1 into the run dir
python -m rsi $S collect --task random_acts_of_pizza --round 1 <run_dir from submit.json>
python tools/acceptance_harness.py --state .state-harness --task random_acts_of_pizza --round 0 --pytest
```

`collect` refuses (after recording `reward.json` and `harness_receipt.json`, without writing memory) a run whose
adapter load event is missing or whose loaded bytes differ from the published manifest. `--reused` marks an
older run started outside the state (no adapter events) as round-0 evidence; it is not charged again.

## Budget

`budget.json`: cap for this state, plus `history_usd` from earlier states (reported as cumulative). Spent = each
collected run's `cost.txt` + $3 reserved per uncollected run + improver sessions. `submit` refuses if spent + $3
> cap; `improve` gets at most what is left. List-price estimates from logged tokens.

## Train / test

Train tasks write memory. Test tasks and replicates (`--rep`) never write memory; `improve` refuses test tasks.

## Known limits

- One run per round; a single run's vector is weak evidence, and the official score of training tasks has been
  seen by the outer loop, so it is not independent evidence of generalisation.
- The hook can only add text to failed nodes' output; whether AIDE uses it is observed, not guaranteed.
- `train_only_field` and `preprocessing_outside_cv` are static checks (lower bounds / heuristics).
