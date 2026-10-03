# Three boundary checks added after the first harness acceptance (2026-10-03, offline)

Requested after [harness_result_20261003.md](harness_result_20261003.md). No paid runs. This changes the frozen
method (adapter, harness, candidate, improver code and tests), so `.state-harness` (method `f7c61660bdaf`) stays as
it was; a future real run needs a new state, `rsi freeze`, and an overlay rebuilt with the new adapter
(`tools/build_overlay_v4.sh` with a new `DST_OVERLAY`), because the receipt requires the adapter in the overlay to
be byte-identical to `rsi/harness_adapter.py`.

## 1 Published versions cannot be modified, replaced or deleted by candidate code

Candidate code means the hook: it is the only candidate content that is ever executed (by the candidate checks
and inside AIDE), always in the sandboxed child process. Permissions alone would not be enough (the same user can
chmod back), so the guarantee comes from the sandbox: the child has no os functions and cannot open, create,
rename, remove, chmod or link anything, nor start processes. In addition:

- `publish` now also makes the version directory itself 0555 (was 0700; files 0444 and `hooks/` 0555 already).
- every publish appends to `harness/versions.jsonl`; `Loop.harness(t)` checks files, manifest and ledger, so a
  version deleted and re-created from outside with a self-consistent manifest is refused.

Tests (`tests/test_sandbox.py`): 13 attacks (chmod, overwrite, remove, rename, rmtree, replace, symlink,
`subprocess rm -rf`, `os.system`, `ctypes unlink`, `eval(__import__)`, `builtins.open`, `io.open`) all fail and
leave inode, mode and bytes of every file in the version unchanged; the same with the static rules switched off and
the hooks pushed through all four candidate-check stages; ledger catches a forged replacement; directory modes.

## 2 The hook reads only its event and creates no files

- Input: `run_hook` passes only `EVENT_KEYS` (interface, exc_type, exc_message, traceback_tail, code); extra keys
  are dropped. Source and event come on stdin.
- Reads: any `open` (any path, any mode, also `io.open`, `pathlib`, `codecs`, `linecache`, `os.open`) is refused by
  the audit hook; os functions do not exist; environment is empty; `/proc/self/environ` cannot be opened.
- Writes: no bytes (RLIMIT_FSIZE 0), no file creation (open/mkdir/symlink/link/mkfifo/tempfile/touch via
  subprocess or os.system all fail), also not in its own working directory, which is empty and read-only. So the
  "allowed directory" is none; the only output is one JSON line on stdout.

Tests: fake credential file, environment variable, grader file, test-label file and state file, read 7 ways each
plus env/listdir/proc probes: none of the secrets appears in any result; 11 ways to create a file: none succeeds.
Checked locally (macOS, Python 3.11), on CRC (Linux, Python 3.9 venv) and inside the AIDE container (Python 3.11.15,
the interpreter the adapter really uses).

Found while writing these tests: `os.mkfifo` raises no audit event, so the audit hook alone did not stop it; the
runner now also removes every function from `os` and `posix` before the hook runs.

## 3 What `submission_profile` does, and no instructions that rely on invisible state

- `harness.CONFIG_EFFECTS` states each key's real effect (taken from `scripts/_inject_agent_decision.py` and
  `scripts/_inject_submission_stats.py` on CRC) and the improver prompt shows it for the active keys.
  `submission_profile` = `AIDE_SUB_STATS`: after each node executes, a neutral numeric profile of
  `submission/submission.csv` is appended to that node's output; it does not select, filter or rank nodes.
  `tree_topk` / `tree_recent` only decide which nodes keep full detail in decision prompts.
- `harness.INVISIBLE_STATE` rejects text that relies on loop state AIDE cannot see (flagged/unflagged, verifier,
  reward, trusted, official/test score, verifier names, evidence/job ids, loop internals). Applied to notes.md, to
  the hook's message literals (docstrings excluded) and, during candidate check 3, to the messages the hook really
  returns, which covers text assembled at run time.

Tests: the verbatim H1 sentence "prefer an unflagged node ... over a flagged node" and five other cases are
rejected; clean notes (including a column named `flag_count`) pass; a hook that builds "unflagged" from pieces
passes the static check and is rejected by the replay. Against the real `.state-harness` H1: its notes would now
be rejected; its hook alone still passes all checks and answers the three cited errors.

## Result

62 tests pass locally (60 + 2 skipped on CRC). Known remaining limits: `os.stat`-style metadata calls are gone with
the rest of `os`, but a hook can still use CPU up to its limit and return misleading but well-formed text; whether
AIDE follows a note remains an observation, not a guarantee.
