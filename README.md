# rsi-loop

A minimal feedback loop that improves the **harness around AIDE** from execution evidence. The improver can
change instructions, active search settings, and an error-feedback hook. It cannot change AIDE itself, task data,
verifiers, or the loop controller.

## The loop

```text
AIDE run with H_t
    → fixed verifiers + search dynamics + execution receipts
    → memory with traceable evidence IDs
    → improver proposes a harness candidate
    → independent boundary / interface / regression / smoke checks
        ├─ pass: publish H_(t+1) with manifest and version ledger
        └─ fail or no change: keep H_t's contents in the next version
    → next AIDE run
```

The editable harness currently has three files:

| File | Effect |
|---|---|
| `notes.md` | Instructions included in AIDE prompts |
| `config.json` | Settings read by the active AIDE mode |
| `hooks/on_exec_error.py` | After an execution error, `diagnose(event)` returns a note for AIDE's reviewer/debugger |

The hook supplies feedback; AIDE decides how to respond. It does not directly repair code or force a retry.
Search dynamics include node scores, failures, lineage, execution time and hook calls. Model training curves
and per-node token attribution are not currently collected.

## What has been demonstrated

One real harness transition passed the A–E acceptance checks: evidence → modification → independent checks →
actual execution → evaluation. In run `j1500453`, the hook was invoked on 10 failed nodes and returned timeout
advice three times. One note appeared in a debug prompt; the child node removed the expensive search and became
the submitted node.

**This demonstrates a functioning feedback path, not a performance improvement.** Official AUC changed from
0.599 to 0.641, but notes, config and hook changed together, there was one run per version, and an earlier H0
replicate scored 0.646. H0 was reused from an older execution base. Training-task official scores are visible to
the outer improver and therefore are not independent generalisation evidence.

The live acceptance used implementation `bd29a82`; results and evidence were saved in `16daf7e`. Subsequent
boundary fixes (`b0574d9`) passed offline tests but have not repeated the live run. The accepted stage cost $1.74;
cumulative experimental spending at that point was $19.45.

## Start reading here

1. [Real-run result and acceptance table](docs/harness_result_20261003.md).
2. [Compact evidence pack](docs/evidence_harness_20261003/README.md): the candidate diff, checks and execution trail.
3. [Boundary checks and limitations](docs/harness_boundaries_20261003.md).
4. [Operation and implementation reference](docs/operations.md): configuration, CRC setup and commands.

[Failure audit](docs/harness_entry_audit_20261003.md) explains why the error hook was selected.
Earlier experiments and planning documents are retained in [the archive](docs/archive/README.md).

## Repository map

```text
harness/H0/       Initial notes, config and no-op error hook
rsi/             Fixed controller and evaluation code
  loop.py        collect / improve / publish / submit orchestration
  improver.py    Evidence-driven candidate generation
  harness.py     Allowed files, manifests and version publication
  candidate.py   Independent candidate checks
  harness_adapter.py  Fixed AIDE hook integration and restricted child process
  dynamics.py    Search observations and evidence IDs
  verifiers/     Fixed reward checks
  backend.py     CRC SGE submission and delivery verification
  method.py      Method freeze; separate from evolving harness versions
  ...            Budget, memory, task loading, run parsing and model client
tasks/          Fixed ROAP and insults task definitions
tests/          Offline regression tests
tools/          Overlay builder and acceptance scripts
docs/           Results, compact evidence, reference and historical archive
```

Local `.state*/` directories contain version histories, candidates, run records and memory; they are not
committed. Task datasets, container images, overlays, credentials and full run logs are also excluded.

## Offline checks

From the repository root, using Python 3.9+:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[test]'
python -m pytest -q tests
```

Two reproduction tests require optional `scikit-learn` / `nltk` and skip when unavailable. Tests do not launch
paid AIDE runs. Running the live loop additionally requires the external research runner, task data,
SGE/Apptainer infrastructure and a compatible base overlay; see [operations](docs/operations.md).

## Limits

- No demonstrated score improvement or cross-task generalisation.
- Verifiers are heuristics; an unflagged node is not proof of valid evaluation.
- Hook output is advice, not guaranteed agent compliance.
- The child process uses resource limits and Python-level restrictions. Attack regression tests are not proof
  of a general security sandbox. Python audit hooks are not an OS-enforced isolation boundary.
- The version ledger detects changes relative to that ledger; it is not an independent trusted store.
- A method change requires a new state and rebuilt overlay; old acceptance evidence remains historical.
