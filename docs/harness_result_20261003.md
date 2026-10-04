# Harness entry point: implementation and acceptance result (2026-10-03)

Plan: [runtime_loop_implementation_20261003.md](archive/runtime_loop_implementation_20261003.md). Failure audit and choice
of the entry: [harness_entry_audit_20261003.md](harness_entry_audit_20261003.md). Shareable evidence pack (small,
no keys, no data): [evidence_harness_20261003/](evidence_harness_20261003). Full state: CRC
`~/rsi-loop/.state-harness`, mirrored locally in `.state-harness/` (gitignored).

## Conclusion, in three layers

1. **Extension interface: implemented.** `hooks/on_exec_error.py` is a versioned harness file executed by a fixed
   adapter inside AIDE's real path (`Agent.parse_exec_result`, dedicated v4 overlay), with immutable versions,
   manifests, candidate checks, receipts and search dynamics. 46 offline tests pass locally (44 + 2 skipped on CRC,
   whose venv has no sklearn/nltk for the two reproduction tests).
2. **Harness loop: accepted, A-E pass** on one real step (evidence j1496637 -> candidate -> checks -> H1 ->
   real run j1500453 executed H1). The changed hook was called in the run, its note reached the debug prompt,
   and the child node did what the note said.
3. **Performance: no evidence.** One run per harness. Official AUC 0.599 (H0, reused run) -> 0.641 (H1); an earlier
   stock-H0 replicate scored 0.646. The training-task score has been seen by the outer loop, so it is not
   independent evidence either.

## Identifiers

| item | value |
|---|---|
| code | commit `bd29a82` (implementation); this report and evidence pack in the next commit |
| method digest | `f7c61660bdaf` (gpt-5.4 improver; overlay sha256 `c5084259…`; all_files `ff9f45735713`) |
| overlay | `~/rsi-loop-overlays/v4/agent_fixes_v4.overlay` = v3 + `rsi/harness_adapter.py` (sha256 `fbe98931…`), built by job 1500450 with an in-container check |
| H0 / H1 digest | `4c179282cfd5` / `16f381905a92` (H1 manifest parent = H0) |
| round 0 | j1496637, reused: stock H0 run from `.state`, v3 overlay without the adapter, no adapter events (marked as provenance; not charged again) |
| round 1 | j1500453, run dir `runs/random-acts-of-pizza/20261003_041359_rsi_rsi_7eff655add_h16f381905a92` |
| candidate | `candidates/random_acts_of_pizza_r00_16f381905a92` (published; no failed candidates in this session) |

## The example chain

1. **Raw evidence** (j1496637): 9 of 25 nodes failed: 5 `TimeoutError` (about 3,000 of 5,080 s of execution
   time; `RandomizedSearchCV`/`GridSearchCV` fits), 3 "All the N fits failed", 1 invalid `StratifiedKFold`
   arguments. Verifiers: submitted node scored its stacking meta-model in-sample (implausible -1, selection -1).
2. **Evidence ids cited**: `j1496637:step00:error`, `step07:error` (timeout in `lgb_random_search.fit`),
   `step16:error`, `verifier:implausible_validation:1`, `verifier:selection:1`. The improver read the three error
   excerpts with `read_evidence` before writing.
3. **Hypothesis** (improver, "high confidence"): AIDE over-explores stacking and search, which both inflates
   self-reported validation and burns the time budget; it misses simple fixes for common sklearn errors.
4. **Diff** ([candidate_diff.patch](evidence_harness_20261003/candidate_diff.patch)): hook answers three error
   families (timeout in search -> remove/shrink search, keep TF-IDF sparse, prefer TF-IDF + LogisticRegression;
   `StratifiedKFold` shuffle/random_state; mixed column-name types); notes on simple baselines, held-out-only
   validation, preprocessing inside CV; config `tree_topk` 5->3, `tree_recent` 5->7, `submission_profile` on.
5. **Checks**: improver ran `check` once; the controller re-ran all four stages: boundary, isolated import +
   probe, regression (fixed events, determinism, replay of the cited error events through the hook: the timeout
   event got a message), smoke through `harness_adapter.apply`. All passed; H1 published with a manifest.
6. **Next run's behaviour** (j1500453): the adapter loaded H1 (loaded SHA-256s = manifest, adapter SHA = repo
   file). 10 nodes raised; the hook was called 10 times (0 failures) and appended a note 3 times, all for
   `TimeoutError` (steps 11, 14, 22); for the other 7 exception types it returned `None`. Step 11 (LightGBM +
   `RandomizedSearchCV`, timed out) was chosen for debugging; the debug prompt contains the note under
   "Execution output" ([excerpt](evidence_harness_20261003/run_j1500453/debug_prompt_step11_excerpt.txt)). Its child,
   step 23, planned: *"The previous implementation faced a timeout issue during hyperparameter search … I will
   streamline the approach by using a simple Logistic Regression model with TF-IDF … avoids lengthy hyperparameter
   tuning"*; its code has no search. Step 23 scored val 0.684 (the run's best) and was submitted. Steps 14 and 22
   were never debugged; their notes reached only the reviewer.
7. **Reward and dynamics**: below.

## Acceptance table (`tools/acceptance_harness.py --pytest`, exit 0)

| check | result | facts |
|---|---|---|
| A evidence | pass | verifier results and dynamics in memory; 5 cited ids all resolve in evidence.json; every node has `tokens: null, missing_reason: not_attributed`; round 0's provenance recorded |
| B harness modification | pass | outcome edited, published; changed notes.md, config.json, hooks/on_exec_error.py = proposal's changed_paths; observation, hypothesis, expected behaviour, regression risks all present |
| C independent checks | pass | controller re-check 4/4; fixed test suite on CRC 44 passed, 2 skipped; failed-candidate, rollback and controller-override paths covered by tests (`test_failed_candidate_keeps_version_and_is_recorded`, `test_controller_rechecks_what_the_improver_claims`, `test_published_version_is_immutable`) |
| D actual execution | pass | receipt ok, digest match; hook called 10x, appended 3x; note in 4 prompts of aide.verbose.log (reviewer for 11/14/22, debug prompt for 11) and in 2 journal outputs (step 11's journal output is stored as `<OMITTED>` by the research repo's journal writer, so 2 not 3); step 11 -> step 23 followed the note and cleared the error |
| E fixed evaluation | record | see below |

E (same verifiers, same collector; round 0 = reused run under the older base):

| | round 0 (H0, j1496637) | round 1 (H1, j1500453) |
|---|---|---|
| official AUC | 0.599 | 0.641 |
| submitted val | 0.996 (in-sample stacking) | 0.684 |
| implausible / preprocessing / selection | -1 / -0.06 / -1 | 0 / 0 / 0 |
| train_only_field | 0 | -0.04 (1 node) |
| search_health | -0.36 (9/25) | -0.44 (10 errors + 1 buggy without exception) |
| scored nodes | 16 | 14 |
| timeouts | 5 | 3 |
| execution time | 5,080 s | 2,183 s |
| children of failed nodes that no longer show the parent's error | 1 / 4 | 4 / 4 (one of them after a note) |
| run cost | $1.49 (not charged here) | $1.47 |
| failed hook calls / fallback | n/a (no adapter) | 0 / none |

Not attributable: notes, config and hook changed together, and n = 1. The leakage verifiers going to 0 can come
from the notes or from chance (stock H0 was clean in one of two earlier runs). The one mechanism we can trace node
by node is the hook's timeout note -> debug -> step 23.

## Cost

This stage: improver $0.27 + AIDE run $1.47 = **$1.74** of the $15 cap ($13.26 unused; no further paid runs, as
A-E passed). Cumulative actual spending: $17.71 + $1.74 = **$19.45** (cap $32.71). No job running.

## Known defects and limits (not fixed in this state: fixing them changes the frozen method)

Follow-up (same day, offline only): the first three items below are addressed in a new method version, see
[harness_boundaries_20261003.md](harness_boundaries_20261003.md). `.state-harness` keeps its frozen method
`f7c61660bdaf`; the next real run needs a new state and a rebuilt overlay (the adapter changed).


- `harness.publish` leaves the version's top directory at mode 0700 (only files and `hooks/` are read-only). An
  added file would be caught by `tree_problems` and the manifest check, but the directory itself is not
  read-only. Fix in the next method version.
- The hook process can still read files the AIDE process can read and create empty files (no bytes can be
  written); the static rules (no `open`/`os`/introspection) are what prevent it in practice.
- H1's notes say "prefer an unflagged node" (loop vocabulary AIDE cannot act on); this stage has no check for it.
  The improver's proposal also described `submission_profile` as "a more conservative submission profile"; it only
  appends submission statistics to node output.
- Per-node tokens and model training curves are not collected (`null` with reasons). Values printed by node code
  are self-reported.
- 7 of 10 exception types in round 1 (NameError, feature-count mismatch, sparse indexing, missing `vaderSentiment`,
  `lgb.cv` keyword, KeyError on retrieval-only columns) had no rule in the hook.

## Reproduce (CRC)

```bash
cd ~/rsi-loop; P=~/real-rsi-mvp/.venv/bin/python; S="--state .state-harness"
export MLEBENCH_AIDE_ROOT=~/mlebench-aide; set -a; source ~/autoML-pilot/config/run.env; set +a   # key not printed
qsub -q long -pe smp 1 -cwd -j y -o overlay_v4.log -S /bin/bash tools/build_overlay_v4.sh
$P -m rsi $S freeze --model gpt-5.4 --overlay ~/rsi-loop-overlays/v4/agent_fixes_v4.overlay
$P -m rsi $S init
$P -m rsi $S budget --cap 15 --history 17.71 --note "rsi-loop .state (16.12) + .state-accept (1.59)"
$P -m rsi $S collect --task random_acts_of_pizza --round 0 <j1496637 run dir> --reused "<provenance>"
$P -m rsi $S improve --task random_acts_of_pizza --round 0 --max-cost 0.5
$P -m rsi $S submit  --task random_acts_of_pizza --round 1
$P -m rsi $S collect --task random_acts_of_pizza --round 1 <run_dir in submit.json>
$P tools/acceptance_harness.py --state .state-harness --task random_acts_of_pizza --round 0 --pytest
```
