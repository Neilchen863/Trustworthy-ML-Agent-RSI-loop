# Closed-loop acceptance: result (2026-10-03)

Protocol: `acceptance_plan.md`. State: CRC `~/rsi-loop/.state-accept` (local mirror `.state-accept/`, gitignored).
Frozen method `a4c267fd4240` (verifiers e041d278705d, improver 4826002739dd, harness schema 2353df708c16,
improver gpt-5.4). Spend $1.59 (AIDE run $1.47, improver $0.12), cap $3.88. Experiment total so far: $17.71.

**Verdict: pass (A, B, C).** Feedback produced a traceable harness edit that entered the next run.

| check | result | facts |
|---|---|---|
| A evidence in memory | pass | round 0 = job 1496637 (stock H0); 4 verifiers fired, all with concrete evidence lines |
| B traceable, effective edit | pass | H1 quotes `roc_auc_score(y, train_meta_preds)` and `rfecv.fit(X_train_full, y_train)` from the evidence; changed only agent-mode keys (tree_topk 5->3, tree_recent 5->6); no loop vocabulary in notes |
| C delivered | pass | job 1500133 ran H1 (digest match, prompt variant rsi_6b8bc699b5, collect delivery check passed); the first notes line appears in 81 prompts in aide.verbose.log |
| D compliance (n = 1, recorded) | mixed | implausible_validation -1 -> 0 (0/8 nodes > 0.9), preprocessing_outside_cv -0.06 -> 0, selection -1 -> 0; search_health -0.36 -> -0.68 (17/25 buggy) |

Official AUC 0.599 -> 0.624; submitted val 0.649 (val and test agree). Not an effect claim: one run, and the
earlier stock-H0 replicate scored 0.646 with no flags.

## What the D row shows

- AIDE followed the notes in form: 16 of the 17 buggy nodes use Pipeline/ColumnTransformer with
  StratifiedKFold, as H1 asked. Most failures are ValueError (7), LookupError (3), KeyError (3): the
  leak-safe structure was harder for gpt-4o to write correctly, so only 8 of 25 nodes produced a score.
- So in this run, compliance traded validation honesty for search budget. The next improver session would see
  that in search_health with concrete exceptions; this stage stops here as planned.

## Caveats on the checks themselves

- B's "evidence tokens quoted" also matches generic words (Logistic, random, submission). The pass rests on the
  specific ones (`rfecv.fit`, `X_train_full`, `roc_auc_score`); the token filter should drop common words.
- H1's notes again say "AUC > 0.9 is a bug signal", which is the implausible_validation threshold; that verifier
  reading 0 is partly expected from the instruction itself.
- C's prompt count proves the text was in the prompts, not that each LLM call acted on it.
