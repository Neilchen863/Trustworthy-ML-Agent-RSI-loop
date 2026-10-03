"""The improver: an LLM that reads memory and evidence and edits the harness files with tools.

It sees the task description, the memory (every past training round's reward vector, verifier summaries,
search-dynamics summary and evidence index) and the current harness.  It can read evidence excerpts by id,
write only the whitelisted harness files, run the fixed candidate checks (at most MAX_CHECKS times per session)
and finish with a structured proposal.  The loop re-runs the checks before publishing; the improver's own
check result is never trusted."""
from __future__ import annotations

import json

from . import harness as hz
from .llm import LLMError

MAX_CHECKS = 2

SYSTEM = """You improve the harness of an ML engineering agent (AIDE) that solves Kaggle-style tasks.

Each round AIDE runs once on a task with the current harness. Afterwards fixed verifiers score the run, a fixed
collector extracts the search dynamics (which nodes failed, how, whether an error repeated along a branch), and
both are added to memory with evidence ids. Your job: change the harness so that the next run behaves better
on a problem the evidence shows clearly.

The harness has three files. Change one, several or none; edit what the evidence points to.
- harness/notes.md: text added to every prompt AIDE's LLMs see (code writing, debugging, reviewing, choosing
  the node to submit). Up to {max_notes} characters. Empty = stock AIDE.
- harness/config.json: search settings. This task runs in {mode} mode, so only these keys matter (type [min, max]):
{schema}
  The other keys are not read in {mode} mode; check rejects changes to them.
- harness/hooks/on_exec_error.py: `def diagnose(event)` called by a fixed adapter right after a node's code
  raised an exception. event = {{"exc_type": str, "exc_message": str, "traceback_tail": str (last lines of the
  execution output), "code": str (the node's code)}}. Return None (no change) or a string of at most 2000
  characters; the adapter appends it to that node's execution output as "[harness note] <string>". AIDE's
  reviewer reads that output, and the debug step that tries to fix the node gets it in its prompt. The node
  still counts as failed and no budget is added. Rules: only these imports: {imports}; no file, network,
  exec/eval or introspection; runs in an isolated process with a {timeout}s limit and must be deterministic.
  The current version may simply `return None` (stock behaviour).

What AIDE can and cannot see: AIDE never sees verifier names, rewards, flags, evidence ids or words from this
loop. Notes and hook messages must state concrete facts and practices AIDE can act on while writing or fixing
code. Do not write test labels, official scores or score thresholds ("a normal AUC is ...") into the harness.

Evidence: memory lists evidence ids (e.g. "j123:step07:error", "j123:verifier:selection:1") with one-line
observations. read_evidence(id) returns the excerpt (traceback, code, adapter event). "suspected" propagation
means the same error signature as the parent node: it is not an independent occurrence. Values printed by
node code are self-reported, not verified.

Process:
1. Read the evidence that matters. Separate first occurrences from repeats along one branch.
2. State an observation, a hypothesis about its cause (say how sure you are), and the change.
3. Write the files, then call check(evidence_ids). check runs the fixed checks: boundary, isolated import,
   fixed regression events, replay of the execution_error evidence you cite through the hook (a changed hook
   must answer at least one of them), and a smoke test through the real adapter. You have at most {max_checks}
   check calls in this session; after {max_checks} failures the session ends and the harness stays unchanged.
4. finish(...) with: evidence_ids, observation, hypothesis, changed_paths, expected_behavior (what should be
   observable in the next run's dynamics or verifiers), regression_risks (e.g. fewer valid nodes, more time),
   summary (1-4 sentences). If nothing justifies a change, finish without editing; evidence_ids may then be empty.

One run is noisy. Do not chase a single number; act on problems the evidence shows clearly."""

TOOLS = [
    {"type": "function", "function": {
        "name": "read_file", "description": "Read harness/notes.md, harness/config.json, harness/hooks/on_exec_error.py, "
                                            "context/memory.jsonl or context/task.md",
        "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}},
    {"type": "function", "function": {
        "name": "write_file", "description": "Replace the whole content of a harness file",
        "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
                       "required": ["path", "content"]}}},
    {"type": "function", "function": {
        "name": "read_evidence", "description": "Read one evidence item by id (excerpt, source, hook input event)",
        "parameters": {"type": "object", "properties": {"evidence_id": {"type": "string"}},
                       "required": ["evidence_id"]}}},
    {"type": "function", "function": {
        "name": "check", "description": f"Run the fixed candidate checks on the current files (max {MAX_CHECKS} calls)",
        "parameters": {"type": "object", "properties": {"evidence_ids": {"type": "array", "items": {"type": "string"}}},
                       "required": ["evidence_ids"]}}},
    {"type": "function", "function": {
        "name": "finish", "description": "End the session with a structured proposal",
        "parameters": {"type": "object", "properties": {
            "evidence_ids": {"type": "array", "items": {"type": "string"}},
            "observation": {"type": "string"}, "hypothesis": {"type": "string"},
            "changed_paths": {"type": "array", "items": {"type": "string"}},
            "expected_behavior": {"type": "string"}, "regression_risks": {"type": "string"},
            "summary": {"type": "string"}},
            "required": ["evidence_ids", "observation", "hypothesis", "changed_paths", "expected_behavior",
                         "regression_risks", "summary"]}}},
]

PROPOSAL_TEXT = ("observation", "hypothesis", "expected_behavior", "regression_risks", "summary")


def render_summary(memory: list) -> str:
    """Compact table of every round, then the latest round's verifier results, dynamics and evidence index."""
    if not memory:
        return "Memory is empty."
    names = list(memory[-1]["vector"])
    rows = ["| round | harness | " + " | ".join(names) + " | change |", "|" + "---|" * (len(names) + 3)]
    for r in memory:
        cells = ["n/a" if r["vector"].get(n) is None else f"{r['vector'][n]:.3f}" for n in names]
        rows.append(f"| {r['round']} | {r['harness']} | " + " | ".join(cells) + f" | {r.get('change') or '-'} |")
    last = memory[-1]
    detail = [f"\nLatest round {last['round']} ({last['harness']}) on {last['task']}:"]
    for res in last["results"]:
        if not res["applicable"]:
            detail.append(f"- {res['name']}: not applicable ({res['summary']})")
            continue
        detail.append(f"- {res['name']} = {res['reward']:.3f}: {res['summary']}")
        detail += [f"    * {e}" for e in res["evidence"]]
    dyn = last.get("dynamics")
    if dyn:
        s = dyn["summary"]
        detail.append(f"\nSearch dynamics ({last.get('dynamics_source') or 'collected'}): {s['n_nodes']} nodes, "
                      f"{s['n_scored']} scored, {s['n_error']} errors ({s['n_errors_first_in_lineage']} first in their "
                      f"lineage, {s['n_errors_suspected_propagation']} suspected propagation); children of error "
                      f"nodes: {s['children_of_error_nodes']}, of which {s['children_clearing_parent_error']} no longer "
                      f"show the parent's error; harness hook calls: {s['harness_calls']} "
                      f"({s['harness_calls_appended']} appended a note, {s['harness_calls_failed']} failed)")
        detail.append(f"error types: {s['error_types']}")
        detail.append("Evidence index (read_evidence(id) for the excerpt):")
        detail += [f"- {e['evidence_id']} [{e['kind']}, {e['confidence']}]: {e['observation']}"
                   for e in dyn["evidence_index"]]
        if dyn.get("evidence_omitted"):
            detail.append(f"- ... {dyn['evidence_omitted']} more items not listed")
    return "\n".join(rows + detail)


def _evidence_view(e: dict) -> str:
    view = {k: e.get(k) for k in ("evidence_id", "kind", "confidence", "observation", "source", "excerpt")}
    if e.get("event"):
        ev = e["event"]
        view["hook_input_event"] = {"exc_type": ev["exc_type"], "exc_message": ev["exc_message"][:1000],
                                    "traceback_tail": ev["traceback_tail"][-2500:], "code": ev["code"][:6000]}
    return json.dumps(view, ensure_ascii=False, indent=1)


class Improver:
    def __init__(self, llm, max_steps=24):
        self.llm, self.max_steps = llm, max_steps

    def improve(self, files: dict, memory: list, task_md: str, mode: str, evidence=(), checker=None) -> dict:
        """Returns {"outcome": edited|no_change|check_failed|unfinished|error, "files", "proposal", "checks",
        "transcript", ...}.  `checker(files, evidence_ids)` is the fixed candidate check."""
        from . import harness_adapter as ha
        work = dict(files)
        store = {e["evidence_id"]: e for e in evidence}
        context = {"context/memory.jsonl": "\n".join(json.dumps(r, ensure_ascii=False) for r in memory),
                   "context/task.md": task_md}
        schema = "\n".join(f"  {k}: {hz.CONFIG_SCHEMA[k][0].__name__} [{hz.CONFIG_SCHEMA[k][1]}, {hz.CONFIG_SCHEMA[k][2]}]"
                           for k in hz.active_keys(mode))
        system = SYSTEM.format(max_notes=hz.MAX_NOTES_CHARS, schema=schema, mode=mode, max_checks=MAX_CHECKS,
                               imports=", ".join(sorted(hz.HOOK_IMPORTS)), timeout=int(ha.HOOK_TIMEOUT))
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": "Memory summary:\n\n" + render_summary(memory)
             + "".join(f"\n\nCurrent harness/{f}:\n```\n{work[f] or '(empty)'}\n```" for f in hz.FILES)},
        ]
        st = {"checks": [], "passed_files": None, "proposal": None, "stop": None}
        transcript = []
        try:
            for step in range(1, self.max_steps + 1):
                msg = self.llm.chat(messages, TOOLS)
                calls = msg.get("tool_calls") or []
                messages.append({"role": "assistant", "content": msg.get("content"),
                                 **({"tool_calls": calls} if calls else {})})
                transcript.append({"step": step, "text": msg.get("content"),
                                   "calls": [[c["function"]["name"], c["function"]["arguments"]] for c in calls]})
                if not calls:
                    messages.append({"role": "user", "content": "Call a tool, or finish(...)."})
                    continue
                for c in calls:
                    result = self._call(c["function"]["name"], c["function"]["arguments"], work, files, context,
                                        store, mode, checker, st)
                    messages.append({"role": "tool", "tool_call_id": c["id"], "content": result})
                    if st["proposal"] is not None or st["stop"]:
                        break
                if st["proposal"] is not None or st["stop"]:
                    break
        except LLMError as exc:
            return {"outcome": "error", "reason": str(exc), "files": files, "checks": st["checks"],
                    "transcript": transcript}
        if st["stop"]:
            return {"outcome": "check_failed", "reason": st["stop"], "files": files, "candidate_files": work,
                    "checks": st["checks"], "transcript": transcript}
        if st["proposal"] is None:
            return {"outcome": "unfinished", "files": files, "candidate_files": work, "checks": st["checks"],
                    "transcript": transcript}
        changed = work != files
        return {"outcome": "edited" if changed else "no_change", "files": work, "proposal": st["proposal"],
                "summary": st["proposal"].get("summary"), "checks": st["checks"], "transcript": transcript}

    @staticmethod
    def _call(name, raw_args, work, base, context, store, mode, checker, st) -> str:
        try:
            args = json.loads(raw_args or "{}")
        except json.JSONDecodeError:
            return "error: arguments are not valid JSON"
        path = str(args.get("path", ""))
        fname = path.split("/", 1)[1] if path.startswith("harness/") else None
        if name == "read_file":
            if fname in hz.FILES:
                return work[fname] or "(empty)"
            return context.get(path, f"error: no such file {path!r}")
        if name == "write_file":
            if fname not in hz.FILES:
                return f"error: only {['harness/' + f for f in hz.FILES]} are writable, not {path!r}"
            work[fname] = str(args.get("content", ""))
            return f"wrote {path} ({len(work[fname])} chars)"
        if name == "read_evidence":
            e = store.get(str(args.get("evidence_id", "")))
            return _evidence_view(e) if e else f"error: unknown evidence id {args.get('evidence_id')!r}"
        if name == "check":
            return Improver._run_check(work, list(args.get("evidence_ids") or []), store, checker, st)
        if name == "finish":
            changed = [f for f in hz.FILES if work.get(f) != base.get(f)]
            prop = {k: args.get(k) for k in ("evidence_ids", "changed_paths", *PROPOSAL_TEXT)}
            prop["evidence_ids"] = [str(x) for x in (prop["evidence_ids"] or [])]
            prop["changed_paths"] = [str(x).removeprefix("harness/") for x in (prop["changed_paths"] or [])]
            problems = []
            if changed:
                unknown = [i for i in prop["evidence_ids"] if i not in store]
                if not prop["evidence_ids"]:
                    problems.append("evidence_ids is empty; cite the evidence the change is based on")
                if unknown:
                    problems.append(f"unknown evidence ids {unknown}")
                if sorted(prop["changed_paths"]) != sorted(changed):
                    problems.append(f"changed_paths {prop['changed_paths']} != files actually changed {changed}")
                problems += [f"{k} is empty" for k in PROPOSAL_TEXT if not str(prop.get(k) or "").strip()]
            elif not str(prop.get("summary") or "").strip():
                problems.append("summary is empty; say why nothing changed")
            if problems:
                return "cannot finish:\n" + "\n".join(problems)
            if changed and st["passed_files"] != work:
                res = Improver._run_check(work, prop["evidence_ids"], store, checker, st)
                if not res.startswith("ok"):
                    return res
            st["proposal"] = prop
            return "finished"
        return f"error: unknown tool {name!r}"

    @staticmethod
    def _run_check(work, ids, store, checker, st) -> str:
        if len(st["checks"]) >= MAX_CHECKS:
            st["stop"] = f"check limit ({MAX_CHECKS}) reached"
            return st["stop"]
        if checker is None:
            problems = hz.validate(work)
            res = {"passed": not problems, "checks": [{"check": "validate", "passed": not problems,
                                                        "detail": problems or None}]}
        else:
            res = checker(dict(work), ids)
        st["checks"].append({"evidence_ids": ids, "result": res})
        if res["passed"]:
            st["passed_files"] = dict(work)
            return "ok: " + json.dumps([[c["check"], c["passed"]] for c in res["checks"]])
        failed = [c for c in res["checks"] if not c["passed"]]
        if len(st["checks"]) >= MAX_CHECKS:
            st["stop"] = f"check failed {MAX_CHECKS} times: {json.dumps(failed, default=str)[:1500]}"
            return st["stop"]
        return (f"check failed ({len(st['checks'])}/{MAX_CHECKS} used): "
                + json.dumps(failed, default=str, ensure_ascii=False)[:3000])
