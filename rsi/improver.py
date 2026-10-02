"""The improver: an LLM that reads memory and edits the harness files with tools.

It sees the task description, the full memory (every past round's harness change and reward vector) and the
current harness.  It can only write notes.md and config.json; `check` validates them, `finish` ends the
session.  The loop validates again before saving H_{t+1}."""
from __future__ import annotations

import json

from . import harness as hz
from .llm import LLMError

SYSTEM = """You improve the harness of an ML engineering agent (AIDE) that solves Kaggle-style tasks.

Each round AIDE runs once on a task with the current harness. Afterwards fixed verifiers score the run and
the result is added to memory. Your job: edit the harness so that the next run scores better.

The harness has two files:
- notes.md: text added to every prompt AIDE's LLMs see (code writing, debugging, reviewing, choosing the
  node to submit). Up to {max_notes} characters. Empty = stock AIDE.
- config.json: search settings. This task runs in {mode} mode, so only these keys matter (type [min, max]):
{schema}
  The other keys in the file are not read in {mode} mode; check() rejects changes to them.

What AIDE can and cannot see: AIDE reads notes.md as plain instructions. It never sees verifier names,
rewards, flags or words like "trusted"/"implausible" from this loop, so notes that say "avoid flagged
nodes" give it nothing to act on. Notes must describe concrete practices AIDE can follow while writing,
debugging, reviewing or choosing code (for example how validation must be computed, which data a step may
be fitted on, what to check before trusting a score).

The reward vector (higher is better):
- official_score: the official test metric of the submitted predictions (raw value).
- every other verifier: in [-1, 0]; 0 = no problem found, -1 = worst. Each comes with a summary and evidence.

Rules:
- Base every change on evidence in memory. The evidence includes code lines from the flagged nodes: use them
  to identify the concrete mechanism behind a verifier result, and name that mechanism in your summary.
- You may rewrite or delete existing notes; you are not limited to appending.
- AIDE never sees test labels or the official score; do not ask it to.
- One run is noisy. Do not chase a single number; look for problems the evidence shows clearly.
- If nothing justifies a change, call finish without editing and say why.

Tools: read_file / write_file on harness/notes.md and harness/config.json, read_file on context files,
check() to validate, finish(summary) when done. summary = what you changed and why, in 1-4 sentences."""

TOOLS = [
    {"type": "function", "function": {
        "name": "read_file", "description": "Read a file: harness/notes.md, harness/config.json, "
                                            "context/memory.jsonl or context/task.md",
        "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}},
    {"type": "function", "function": {
        "name": "write_file", "description": "Replace the whole content of harness/notes.md or harness/config.json",
        "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
                       "required": ["path", "content"]}}},
    {"type": "function", "function": {
        "name": "check", "description": "Validate the current harness files", "parameters": {"type": "object",
                                                                                            "properties": {}}}},
    {"type": "function", "function": {
        "name": "finish", "description": "End the session",
        "parameters": {"type": "object", "properties": {"summary": {"type": "string"}}, "required": ["summary"]}}},
]


def render_summary(memory: list) -> str:
    """Compact table of every round, then the full results of the latest round."""
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
    return "\n".join(rows + detail)


class Improver:
    def __init__(self, llm, max_steps=20):
        self.llm, self.max_steps = llm, max_steps

    def improve(self, files: dict, memory: list, task_md: str, mode: str) -> dict:
        """Returns {"outcome": edited|no_change|unfinished|error, "files", "summary", "transcript", ...}."""
        work = dict(files)
        context = {"context/memory.jsonl": "\n".join(json.dumps(r, ensure_ascii=False) for r in memory),
                   "context/task.md": task_md}
        schema = "\n".join(f"  {k}: {hz.CONFIG_SCHEMA[k][0].__name__} [{hz.CONFIG_SCHEMA[k][1]}, {hz.CONFIG_SCHEMA[k][2]}]"
                           for k in hz.active_keys(mode))
        messages = [
            {"role": "system", "content": SYSTEM.format(max_notes=hz.MAX_NOTES_CHARS, schema=schema, mode=mode)},
            {"role": "user", "content": "Memory summary:\n\n" + render_summary(memory)
             + "\n\nCurrent harness/notes.md:\n```\n" + (work["notes.md"] or "(empty)") + "\n```"
             + "\nCurrent harness/config.json:\n```\n" + work["config.json"] + "```"},
        ]
        transcript, summary = [], None
        try:
            for step in range(1, self.max_steps + 1):
                msg = self.llm.chat(messages, TOOLS)
                calls = msg.get("tool_calls") or []
                messages.append({"role": "assistant", "content": msg.get("content"),
                                 **({"tool_calls": calls} if calls else {})})
                transcript.append({"step": step, "text": msg.get("content"),
                                   "calls": [[c["function"]["name"], c["function"]["arguments"]] for c in calls]})
                if not calls:
                    messages.append({"role": "user", "content": "Call a tool, or finish(summary)."})
                    continue
                for c in calls:
                    result = self._call(c["function"]["name"], c["function"]["arguments"], work, context, mode, files)
                    if c["function"]["name"] == "finish" and result.startswith("finished"):
                        summary = json.loads(c["function"]["arguments"] or "{}").get("summary", "")
                    messages.append({"role": "tool", "tool_call_id": c["id"], "content": result})
                if summary is not None:
                    break
        except LLMError as exc:
            return {"outcome": "error", "reason": str(exc), "files": files, "transcript": transcript}
        if summary is None:
            return {"outcome": "unfinished", "files": files, "transcript": transcript}
        changed = work != files
        return {"outcome": "edited" if changed else "no_change", "files": work, "summary": summary,
                "transcript": transcript}

    @staticmethod
    def _call(name, raw_args, work, context, mode=None, base=None) -> str:
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
                return f"error: only harness/notes.md and harness/config.json are writable, not {path!r}"
            work[fname] = str(args.get("content", ""))
            return f"wrote {path} ({len(work[fname])} chars)"
        if name == "check":
            problems = hz.validate(work, mode, base)
            return "ok" if not problems else "problems:\n" + "\n".join(problems)
        if name == "finish":
            problems = hz.validate(work, mode, base)
            return "finished" if not problems else "cannot finish, fix first:\n" + "\n".join(problems)
        return f"error: unknown tool {name!r}"
