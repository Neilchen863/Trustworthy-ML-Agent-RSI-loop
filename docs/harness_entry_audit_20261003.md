# Failure audit and choice of the first harness entry point (2026-10-03)

Source: journals of three ROAP runs, re-read with `rsi/dynamics.py` (error signature = exception type +
normalised first message line + innermost line of the node's own code; "suspected propagation" = same
signature as the parent node). Grouping into families below is manual, from the tracebacks.

## j1500133 (acceptance round 1, H1 notes from `.state-accept`): 17 of 25 nodes failed

| family | steps | first occurrences | repeats along a branch |
|---|---|---|---|
| NLTK data unavailable in the container (`LookupError` vader_lexicon / punkt_tab; `nltk.download` -> `OSError: Read-only file system`) | 4, 5, 12, 16, 23 | 4 (parent 3), 12 (debug of the timeout at 11 added `nltk.download`), 16 (parent 9) | 5 (debug of 4 tried to download), 23 (debug of 16, same signature) |
| ColumnTransformer / FunctionTransformer / param-grid usage (`KeyError: 'request_text_edit_aware'` inside a FunctionTransformer given one column name, dimension mismatch, invalid column spec, `'C'` instead of `classifier__C`) | 7, 10, 14, 17, 20, 21, 22 | 7, 10, 14, 20, 21, 22 | 17 (debug of 7, same signature) |
| dtype conversion (`'N/A'` to float, text column fed as numeric) | 13, 18 | 13, 18 | - |
| other: sparse matrix indexing (0), timeout (11), inconsistent sample counts (19) | 0, 11, 19 | all | - |

By the signature rule: 15 first in their lineage, 2 suspected propagation (17 <- 7, 23 <- 16). Children of
failed nodes: 10, of which 8 no longer show the parent's signature (some fail differently, e.g. 4 -> 5).

Reading: the earlier summary "16 of the 17 failures used a Pipeline" does not mean the Pipeline caused them.
At most 7 failures are misuse of sklearn's composition API (plus 2 dtype errors raised inside a pipeline);
5 are an environment fact (no NLTK data, no network, read-only home) that has nothing to do with Pipeline.
The debugger did not know that fact: twice its fix was `nltk.download(...)`, which fails again.

## j1496637 (stock H0, `.state` round 0): 9 of 25 failed

5 `TimeoutError` (600 s exec limit) inside `RandomizedSearchCV`/`GridSearchCV`/pipeline fits (steps 7, 8, 9, 17, 21;
8 repeats 7), 3 `ValueError: All the N fits failed` (0, 1, 2; 1-2 repeat 0), 1 invalid `StratifiedKFold`
arguments (16). Timeouts used about 3,000 of the 5,080 s of execution time. Children of failed nodes: 4, of
which 1 no longer shows the parent's error.

## j1498166 (stock H0, replicate): 12 of 25 failed

Missing files / modules (`FileNotFoundError` x3 incl. a word2vec file, `ModuleNotFoundError: mlxtend`),
3 timeouts, 3 `ValueError`, `KeyError` on retrieval-only columns, `NameError`.

## Choice of the entry point

Across the three runs, failures are where most of the step budget goes (9-17 of 25 nodes), and the repeat
pattern is the same: the debug step gets the traceback but not the environment facts or API rules needed to
fix it, so it retries the same approach (NLTK download on a read-only, offline machine; the same search that
timed out). AIDE's only channel into that step is the node's execution output.

So the first entry is **`on_exec_error`**: after a node's code raised, a versioned hook gets the exception,
traceback tail and code, and may return a short note that the fixed adapter appends to that node's output
(`[harness note] ...`). It is enforceable (it sits in `Agent.parse_exec_result`, the path every executed node
takes), it is consumed by the real path (the reviewer and the debug prompt of the child read the output), its
effect is observable per node (adapter events, journal output, prompts, whether the child clears the error),
and H0 (`return None`) is exactly stock AIDE.

Offline reproduction (tests/test_harness_entry.py): the FunctionTransformer `KeyError` of steps 7/17/20/21 and
the NLTK `LookupError` of steps 4/16/23 are reproduced with sklearn / nltk on synthetic data, and their
signatures and hook input events are extracted the same way as from the real journal.

Not done: no change was made because "Pipeline" appeared in the failing code; no online node elimination, no
search policy change by the maintainer. Which error family the hook addresses is the improver's decision from
the evidence in memory.
