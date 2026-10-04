# Operation and implementation reference

For the project overview and reading order, see [README](../README.md).

## What the improver may change (the harness)

| path | reaches AIDE through | notes |
|---|---|---|
| `notes.md` | `PROMPT_VARIANT` notes file → every AIDE prompt | ≤ 8000 chars |
| `config.json` | env vars (`tree_topk`, `tree_recent` in agent mode; rule-mode keys; `submission_profile`) | keys of the other mode are frozen |
| `hooks/on_exec_error.py` | the fixed adapter in the dedicated v4 overlay calls `diagnose(event)` after a node's code raised; a returned string is appended to that node's execution output as `[harness note] ...`, which AIDE's reviewer and the debug step for that node read | stdlib allowlist (`re json math textwrap collections itertools functools string`), no file/exec/introspection, ≤ 2000-char output, isolated process, 10 s |

Nothing else: not the verifiers, tasks, data, budget, tests, adapter, candidate checks, collector, memory, the
improver itself, AIDE or the overlay. `manifest.json` in each version is written by the controller.

The entry point (`rsi/harness_adapter.py`, interface `on_exec_error/1`): input
`{interface, exc_type, exc_message, traceback_tail, code}` of the failed node (no data, no labels); output `None` or a
string; failure (timeout, crash, invalid output) = nothing appended, recorded. No step, time or token budget is
added and the node stays failed. Every load and call is a line in `<run>/logs/harness_events.jsonl` with
SHA-256s of the loaded files and the adapter. H0's hook returns `None` (stock behaviour).

Isolation of the hook process (`harness_adapter._RUNNER`): `python -I -S -B`, empty environment, empty read-only
working directory, CPU / memory / process limits, `RLIMIT_FSIZE=0`, wall-clock timeout. Source and event arrive on
stdin (the hook never gets a path); only the five event fields are passed. Before the hook runs, every function is
removed from `os`/`posix` and a PEP 578 audit hook is installed that refuses any file open (read or write), os
calls, subprocess, socket, ctypes, new imports and exec of other code. The tested read/write and process
attacks were blocked locally and inside the AIDE container. These are Python-level execution restrictions,
not a general security sandbox: Python audit hooks do not provide such a guarantee. See the
[boundary report](harness_boundaries_20261003.md). Code generation in the stdlib is
unavailable too (e.g. `collections.namedtuple`).

Published versions: files 0444, directories (including the version directory) 0555, and every publish is recorded
in `harness/versions.jsonl` (digest, file SHA-256s, manifest SHA-256). `Loop.harness(t)` refuses a version that
differs from its manifest or from the ledger, so a directory replaced from outside with a self-consistent manifest
is caught when the separate ledger remains intact; the ledger is not an external trust anchor.

Instructions that rely on state AIDE cannot see are rejected (`harness.INVISIBLE_STATE`: flagged/unflagged,
verifier, reward, trusted, official/test scores, verifier names, evidence/job ids, loop internals), in notes.md, in
the hook's message literals, and in the messages the hook actually returns during the candidate checks.

What each config key really does is in `harness.CONFIG_EFFECTS` and shown to the improver; e.g.
`submission_profile` only appends a neutral numeric profile of submission.csv to each node's output; it does not
select, filter or rank nodes.

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

This repository does not bundle AIDE, MLE-bench data, the research runner, container image, or base v3 overlay.
The current backend requires the CRC SGE/Apptainer setup and a separate MLE-bench_AIDE checkout containing
`sge/submit.sh`, `scripts/run_aide.sh` and `scripts/_common.sh`. Supply the task data and base overlay there.
Set `OPENAI_API_KEY` in your environment without committing it. The build script accepts `AIDE_ROOT`,
`RSI_LOOP_ROOT`, `SRC_OVERLAY`, and `DST_OVERLAY`; its default paths reflect the original research setup.

The historical `.state-harness` and v4 overlay belong to the accepted method. For current code, use a new
state and a new overlay destination; do not overwrite the old run. The commands below are a template,
not a portable one-command reproduction. Paid steps require an appropriate project budget.


```bash
python -m pytest -q tests
export MLEBENCH_AIDE_ROOT=~/mlebench-aide
export DST_OVERLAY=~/rsi-loop-overlays/current/agent.overlay  # must not already exist
qsub -q long -pe smp 1 -cwd -j y -o overlay_v4.log -S /bin/bash tools/build_overlay_v4.sh
# Wait for the build and in-container check to succeed before freezing.
S=--state=.state-review
python -m rsi $S freeze --model gpt-5.4 --overlay "$DST_OVERLAY"
python -m rsi $S init
python -m rsi $S budget --cap 15 --history 17.71 --note "earlier states"
python -m rsi $S collect --task random_acts_of_pizza --round 0 <old run dir> --reused "<where it comes from>"
python -m rsi $S improve --task random_acts_of_pizza --round 0 --max-cost 0.5     # -> candidate -> H1 or copy
python -m rsi $S submit  --task random_acts_of_pizza --round 1 [--dry-run]       # stages H1 into the run dir
python -m rsi $S collect --task random_acts_of_pizza --round 1 <run_dir from submit.json>
python tools/acceptance_harness.py --state .state-review --task random_acts_of_pizza --round 0 --pytest
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
