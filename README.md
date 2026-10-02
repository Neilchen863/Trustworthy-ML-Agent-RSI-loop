# rsi-loop

```
task env + harness H_t ──> AIDE run ──> verifiers ──> reward vector ──> memory
                                                                          │
                     H_{t+1} <── LLM edits notes.md / config.json <───────┘
```

One round = one AIDE run with harness `H_t`, scored by fixed verifiers, written to memory. Then an LLM reads
the whole memory and edits the harness. Nothing else.

## Layout

```
tasks/<task>/          one environment per task
  task.json            competition id, role (train|test), metric, AIDE budget, train-only fields
  instruction.md       short description (also shown to the improver)
  prepare.sh           prepares the data on CRC through the research repo
harness/H0/            the starting harness: notes.md (empty = stock AIDE) + config.json
rsi/
  run.py               reads a finished AIDE run directory
  verifiers/           one file per verifier, all `verify(run, task) -> Result`
  memory.py            memory.jsonl, one line per round
  improver.py          LLM with read/write/check/finish tools over the harness
  backend.py           submits to SGE through the research repo's sge/submit.sh
  loop.py, cli.py      init / submit / collect / improve / score
tests/                 offline, synthetic run directories
```

## Verifiers (the reward vector)

| name | value | reward |
|---|---|---|
| `official_score` | official MLE-bench metric | raw score; **train tasks only** |
| `submission_sanity` | submitted file present, valid, non-constant | 0 or -1 |
| `train_only_field` | share of nodes whose code reads a field missing from test | -share; -1 if the submitted node does |
| `implausible_validation` | share of scored nodes with val beyond `implausible_val` | -share; -1 if submitted |
| `metric_mismatch` | share of nodes whose recorded metric is out of range or not in the printed output | -share; -1 if submitted |
| `selection` | submitted node vs best *trusted* node (no flag above) | -gap; -1 if buggy, or flagged while a trusted node existed |
| `search_health` | share of crashed nodes | -share |

All but `official_score` lie in [-1, 0] with 0 = no problem. A verifier that cannot check something
returns `applicable: false` (reward `null`), never 0. Each result has a summary and evidence lines; that
text is what the improver reads. Add a verifier by adding a module to `rsi/verifiers/` and to `ALL`.

## Harness

- `notes.md` → reaches every AIDE prompt through the research repo's `PROMPT_VARIANT` notes file.
- `config.json` → `tree_topk`, `tree_recent` (agent mode); `max_stagnation`, `debug_prob`, `max_debug_depth`,
  `num_drafts` (rule mode); `submission_profile` (`AIDE_SUB_STATS`). Bounds in `rsi/harness.py`.

`collect` refuses a run that did not receive its harness (prompt variant and notes are checked).

## Use

```bash
python -m pytest -q tests                        # offline
python -m rsi tasks
python -m rsi score --task random_acts_of_pizza <run_dir>     # verifiers on any run, no state

# on CRC, with MLEBENCH_AIDE_ROOT, RSI_OVERLAY_PATH and OPENAI_API_KEY set
python -m rsi init
python -m rsi submit  --task random_acts_of_pizza --round 0 [--dry-run]
python -m rsi collect --task random_acts_of_pizza --round 0 <run_dir>
python -m rsi improve --task random_acts_of_pizza --round 0 --max-cost 0.5     # -> H1
python -m rsi submit  --task random_acts_of_pizza --round 1
...
```

State lives in `.state/` (`--state` to change): `harness/H*`, `rounds/<task>/round_NN/{submit,reward,improve}.json`,
`memory.jsonl`. Round `t` always uses `H<t>`. When the improver changes nothing, `H<t+1>` is a copy.

## Train / test

Train tasks write their official score into memory. Test tasks (`insults`) can be run and collected, but
their score is withheld from `reward.json`, they never write memory, and `improve` refuses them.

## Overlay

Use an overlay that carries the metric-parser fix and the agent-decision prompt fix (`RSI_OVERLAY_PATH`).
The shared overlay on CRC has both bugs: "5-fold" recorded as AUC 5.0, and every agent-mode decision falling
back to the rule policy.

## Known limits

- One run per round: a single run is noisy, so one round's vector is weak evidence.
- `train_only_field` is a static lower bound (code that grabs all columns without naming them is missed).
- Only the submitted node's predictions are kept by AIDE, so `submission_sanity` checks only that file.
