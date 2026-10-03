# Closed-loop acceptance (after the first experiment)

Goal of this stage: feedback drives a traceable harness edit that actually enters the next run. Whether the
official score improves is left to a later experiment. No online blocking or node filtering is added.

## Method changes before freezing

- `preprocessing_outside_cv` now follows data flow: fits inside a loop over `.split(...)` and fits after every
  CV use (final refit) are ignored; a fit counts as confirmed only if its output reaches a later CV use through
  assignments or `.transform`; a fit before CV whose flow cannot be traced is reported as suspect and does not
  lower the reward.
- `selection` and all evidence say "unflagged", not "trusted": no flag is not proof of a sound validation.
- `rsi freeze --model <m>` writes `method.json` (verifier code, improver code, harness schema digests and the
  improver model). collect/improve refuse any difference; each record carries the method digest.

## Protocol (new state, frozen method, one real AIDE run)

1. `rsi --state .state-accept init` (stock H0); `rsi --state .state-accept freeze --model gpt-5.4`;
   `rsi --state .state-accept budget --cap 3.88` (keeps the total under the original $20).
2. Round 0 = an existing H0 run with concrete failures (job 1496637: in-sample stacking score, submitted node
   flagged). `collect --round 0` on it; no new spend.
3. `improve --round 0` -> H1 (gpt-5.4, frozen prompt).
4. `submit --round 1`, wait, `collect --round 1` (delivery check: prompt variant, notes text, sub_stats).
5. `python tools/acceptance.py --state .state-accept --task random_acts_of_pizza --round 0`:
   A evidence in memory, B traceable and effective edit, C delivered (digest + notes found in the prompts AIDE
   sent), D compliance per fired verifier (recorded, n = 1).

Pass = A, B and C pass. D is recorded either way.
