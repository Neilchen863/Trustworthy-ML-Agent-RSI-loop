"""Fixed adapter between AIDE and the versioned harness hook `hooks/on_exec_error.py`.

This file is part of the frozen method (the improver cannot edit it).  The same file is used in two places:

  * inside the AIDE container: tools/build_overlay_v4.sh installs it into the aide package of a dedicated
    overlay and appends an auto-apply line to aide/agent.py.  `apply()` wraps Agent.parse_exec_result;
  * offline: candidate checks call `apply()` on a stand-in Agent class and `run_hook()` directly, so the
    smoke test goes through the code path the real run uses.

Entry point (interface on_exec_error/1)
  when      after a node's code has executed with an exception, before AIDE parses the result
  input     event = {"interface", "exc_type", "exc_message", "traceback_tail", "code"}  (strings, truncated)
  output    the hook's `diagnose(event)` returns None or a string; the adapter appends
            "[harness note] <string>" (at most MAX_MESSAGE chars) to the node's execution output, which
            AIDE's reviewer and its debug prompt for the child node read.  Nothing else changes: the node
            stays buggy, no step, time or token budget is added, the exception is not hidden.
  isolation the hook runs in a separate `python -I -S -B` process with an empty environment (no API key), an
            empty read-only working directory, CPU / memory / process limits, RLIMIT_FSIZE = 0, HOOK_TIMEOUT seconds
            wall clock, and a PEP 578 audit hook that refuses every file open (read or write), every os.* call,
            subprocess, socket, ctypes, new imports and exec of other code (see _RUNNER).  The hook's source and
            event arrive on stdin; its only output is one JSON line on stdout.  It therefore cannot read
            credentials, grader files, test labels or state, nor create, modify, replace or delete any file.  Source bytes are read and checked against the manifest once at
            load, then executed from a private copy, so later edits to the staged files have no effect.
  failure   timeout, crash, invalid output -> nothing is appended (stock behaviour) and the event says so.
  audit     one JSON line per load and per call in $RSI_HARNESS_LOG (default /home/logs/harness_events.jsonl):
            file SHA-256s, adapter SHA-256, input/output digests, status, message, duration.

Disabled (pure pass-through) when the harness directory has no manifest.json."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time

ADAPTER_VERSION = 1
INTERFACE = "on_exec_error/1"
HOOK_PATH = "hooks/on_exec_error.py"
MAX_MESSAGE = 2000
HOOK_TIMEOUT = 10.0
LIMITS = {"exc_message": 2000, "traceback_tail": 6000, "code": 20000}
DEFAULT_DIR = "/home/agent/rsi_harness"
DEFAULT_LOG = "/home/logs/harness_events.jsonl"
NOTE_PREFIX = "[harness note] "

EVENT_KEYS = ("interface", "exc_type", "exc_message", "traceback_tail", "code")   # the hook's only input
ALLOWED_MODULES = ("re", "json", "math", "textwrap", "collections", "itertools", "functools", "string")

# Runs in the child process.  Order matters: everything that needs files or new imports happens before the audit
# hook is installed; after that, the only code that runs is the hook's, under the audit hook, until os._exit.
#   1 limits: CPU, memory, file size 0 (no bytes reach any file), no new processes
#   2 read {"source", "event"} from stdin (the hook never gets a path), pre-import the allowed modules, compile
#   3 install an audit hook (PEP 578; it cannot be removed from Python) that refuses: opening any file (read or
#     write, any path), every os.* operation (create/remove/rename/chmod/link/listdir/exec/spawn/kill/env),
#     subprocess, socket, ctypes, imports of anything not already loaded, compile/exec/eval of other code, gc/frame
#     introspection, pickle/marshal loading
#   3b remove every function from os and posix (some os calls, e.g. mkfifo, raise no audit event)
#   4 exec the hook's code object (the one exec the audit hook allows), call diagnose(event), write one JSON line
#     to stdout (no audit event), os._exit(0) (no interpreter shutdown under the audit hook)
_RUNNER = r"""
import sys
try:
    import resource
    for _lim, _val in (("RLIMIT_CPU", 5), ("RLIMIT_FSIZE", 0), ("RLIMIT_NPROC", 0), ("RLIMIT_AS", 512 * 2**20)):
        try:
            resource.setrlimit(getattr(resource, _lim), (_val, _val))
        except (ValueError, OSError, AttributeError):
            pass
except ImportError:
    pass
import json, os
_req = json.loads(sys.stdin.read())
for _m in %(modules)r:
    __import__(_m)
_out, _exit, _dumps = sys.stdout, os._exit, json.dumps
def _emit(obj):
    _out.write(_dumps(obj)); _out.flush(); _exit(0)
try:
    _code = compile(_req["source"], "on_exec_error.py", "exec")
except BaseException as exc:
    _emit({"ok": False, "error": type(exc).__name__ + ": " + str(exc)[:300]})
_DENY = {"open", "import", "compile", "builtins.input", "builtins.breakpoint", "sys._getframe", "sys._current_frames",
         "sys.settrace", "sys.setprofile", "code.__new__", "function.__new__", "marshal.loads", "marshal.load",
         "pickle.find_class", "object.__setattr__", "object.__delattr__"}
_DENY_PREFIX = {"os", "subprocess", "socket", "ctypes", "shutil", "glob", "tempfile", "sqlite3", "urllib", "http",
                "ftplib", "smtplib", "poplib", "imaplib", "nntplib", "telnetlib", "webbrowser", "fcntl", "mmap",
                "resource", "gc", "pty", "signal", "_posixsubprocess", "cpython", "syslog", "msvcrt", "winreg"}
def _audit(event, args, _code=_code):
    if event == "exec":
        if args and args[0] is _code:
            return
        raise PermissionError("sandbox: exec of other code")
    if event in _DENY or event.split(".")[0] in _DENY_PREFIX:
        raise PermissionError("sandbox: " + event)
# Some os/posix calls raise no audit event (e.g. mkfifo, mknod): remove every callable from both modules, so the
# hook has no os-level function at all (the modules above never call os at run time).
import posix
for _mod in (os, posix):
    for _name in list(vars(_mod)):
        if callable(getattr(_mod, _name, None)) and not _name.startswith("__") and not isinstance(getattr(_mod, _name), type):
            try:
                delattr(_mod, _name)
            except (AttributeError, TypeError):
                pass
sys.addaudithook(_audit)
try:
    _ns = {"__name__": "rsi_hook"}
    exec(_code, _ns)
    _res = _ns["diagnose"](_req["event"])
    if _res is not None and not isinstance(_res, str):
        raise TypeError("diagnose must return None or str, not " + type(_res).__name__)
    _emit({"ok": True, "message": _res})
except BaseException as exc:
    _emit({"ok": False, "error": type(exc).__name__ + ": " + str(exc)[:300]})
""" % {"modules": ALLOWED_MODULES}


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def adapter_sha() -> str:
    with open(os.path.abspath(__file__), "rb") as fh:
        return sha256(fh.read())


def make_event(exc_type, exc_info, term_out, code) -> dict:
    """The hook's input, built from what AIDE recorded for the node (no data files, no labels)."""
    if isinstance(term_out, (list, tuple)):
        term_out = "".join(str(x) for x in term_out)
    msg = ""
    if isinstance(exc_info, dict) and exc_info.get("args"):
        msg = " ".join(str(a) for a in exc_info["args"])
    tail = "\n".join(str(term_out or "").splitlines()[-60:])
    ev = {"interface": INTERFACE, "exc_type": str(exc_type or ""), "exc_message": msg, "traceback_tail": tail,
          "code": str(code or "")}
    for k, n in LIMITS.items():
        ev[k] = ev[k][-n:] if k == "traceback_tail" else ev[k][:n]
    return ev


def run_hook(source: bytes, event: dict, timeout: float = HOOK_TIMEOUT) -> dict:
    """Execute diagnose(event) from `source` in the sandboxed child process (see _RUNNER).

    Only the EVENT_KEYS fields of `event` are passed.  Returns {"status": ok|timeout|error|invalid,
    "message": str|None, "detail", "duration_s"}."""
    t0 = time.time()
    payload = json.dumps({"source": source.decode("utf-8", "replace"),
                          "event": {k: str(event.get(k, "")) for k in EVENT_KEYS}})
    with tempfile.TemporaryDirectory(prefix="rsi_hook_") as tmp:
        os.chmod(tmp, 0o500)                                    # empty, read-only working directory
        try:
            proc = subprocess.run([sys.executable, "-I", "-S", "-B", "-c", _RUNNER], input=payload,
                                  capture_output=True, text=True, timeout=timeout, cwd=tmp,
                                  env={"PATH": "/usr/bin:/bin", "PYTHONHASHSEED": "0"})
        except subprocess.TimeoutExpired:
            return {"status": "timeout", "message": None, "detail": f"> {timeout}s", "duration_s": time.time() - t0}
        finally:
            os.chmod(tmp, 0o700)
    dur = round(time.time() - t0, 3)
    try:
        out = json.loads(proc.stdout or "")
    except json.JSONDecodeError:
        return {"status": "error", "message": None, "duration_s": dur,
                "detail": f"exit {proc.returncode}; no JSON on stdout; stderr: {proc.stderr[-300:]}"}
    if not out.get("ok"):
        return {"status": "error", "message": None, "detail": out.get("error"), "duration_s": dur}
    msg = out.get("message")
    if msg is not None and len(msg) > MAX_MESSAGE:
        return {"status": "invalid", "message": None, "duration_s": dur,
                "detail": f"message has {len(msg)} chars; limit {MAX_MESSAGE}"}
    return {"status": "ok", "message": msg if (msg or "").strip() else None, "detail": None, "duration_s": dur}


class Harness:
    """A loaded, verified harness version (bytes held in memory)."""

    def __init__(self, directory: str):
        self.dir = directory
        self.manifest = None
        self.hook = None
        self.problems = []
        mpath = os.path.join(directory, "manifest.json")
        if not os.path.isfile(mpath):
            return
        with open(mpath, "rb") as fh:
            self.manifest = json.loads(fh.read().decode())
        loaded = {}
        for rel, want in sorted(self.manifest.get("files", {}).items()):
            p = os.path.join(directory, rel)
            if os.path.islink(p) or not os.path.isfile(p):
                self.problems.append(f"{rel}: missing or not a regular file")
                continue
            with open(p, "rb") as fh:
                data = fh.read()
            loaded[rel] = sha256(data)
            if loaded[rel] != want:
                self.problems.append(f"{rel}: sha256 {loaded[rel][:12]} != manifest {want[:12]}")
            if rel == HOOK_PATH:
                self.hook = data
        if self.manifest.get("interface", {}).get("on_exec_error") != 1:
            self.problems.append(f"manifest interface {self.manifest.get('interface')} is not {INTERFACE}")
        self.loaded = loaded

    @property
    def enabled(self) -> bool:
        return self.manifest is not None and not self.problems and self.hook is not None


def _log(path: str, rec: dict) -> None:
    rec = {"t": round(time.time(), 3), **rec}
    try:
        with open(path, "a") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except OSError:
        pass


def apply(agent_cls=None, harness_dir=None, log_path=None):
    """Wrap agent_cls.parse_exec_result (default: aide.agent.Agent).  Returns the loaded Harness."""
    harness_dir = harness_dir or os.environ.get("RSI_HARNESS_DIR") or DEFAULT_DIR
    log_path = log_path or os.environ.get("RSI_HARNESS_LOG") or DEFAULT_LOG
    if agent_cls is None:
        from aide.agent import Agent as agent_cls  # noqa: N813
    if agent_cls.__dict__.get("_rsi_harness_patched", False):
        return agent_cls._rsi_harness
    h = Harness(harness_dir)
    if h.manifest is None:
        agent_cls._rsi_harness_patched, agent_cls._rsi_harness = True, h
        return h                                          # no harness staged: stock AIDE
    _log(log_path, {"event": "load", "adapter_version": ADAPTER_VERSION, "adapter_sha256": adapter_sha(),
                    "interface": INTERFACE, "harness_dir": harness_dir,
                    "manifest_digest": h.manifest.get("digest"), "manifest_files": h.manifest.get("files"),
                    "loaded_files": getattr(h, "loaded", {}), "match": not h.problems, "problems": h.problems,
                    "enabled": h.enabled, "fallback": None if h.enabled else "stock behaviour (hook not called)"})
    orig = agent_cls.parse_exec_result
    hook_sha = sha256(h.hook) if h.hook is not None else None

    def parse_exec_result(self, node, exec_result, *args, **kwargs):
        if h.enabled and getattr(exec_result, "exc_type", None):
            rec = {"event": "call", "hook": HOOK_PATH, "hook_sha256": hook_sha, "node_id": str(node.id),
                   "exc_type": exec_result.exc_type}
            try:
                ev = make_event(exec_result.exc_type, getattr(exec_result, "exc_info", None),
                                exec_result.term_out, getattr(node, "code", ""))
                rec["input_sha256"] = sha256(json.dumps(ev, sort_keys=True).encode())
                res = run_hook(h.hook, ev)
                rec.update(status=res["status"], detail=res["detail"], duration_s=res["duration_s"],
                           message=res["message"], appended=False)
                if res["message"]:
                    exec_result.term_out.append("\n" + NOTE_PREFIX + res["message"] + "\n")
                    rec["appended"] = True
            except Exception as exc:                       # never break the run
                rec.update(status="adapter_error", detail=f"{type(exc).__name__}: {exc}", appended=False)
            _log(log_path, rec)
        return orig(self, node, exec_result, *args, **kwargs)

    agent_cls.parse_exec_result = parse_exec_result
    agent_cls._rsi_harness_patched, agent_cls._rsi_harness = True, h
    return h


# --------------------------------------------------------------------------- overlay installation
MARKER = "# >>> rsi-loop harness adapter >>>"


def install() -> None:
    """Run inside the container (agent env) with the overlay mounted writable: copy this file into the aide
    package as _rsi_harness_adapter.py and append an auto-apply hook to aide/agent.py (idempotent)."""
    import shutil

    import aide.agent as agent_mod
    pkg = os.path.dirname(os.path.abspath(agent_mod.__file__))
    shutil.copyfile(os.path.abspath(__file__), os.path.join(pkg, "_rsi_harness_adapter.py"))
    agent_py = os.path.join(pkg, "agent.py")
    with open(agent_py) as fh:
        src = fh.read()
    if MARKER not in src:
        with open(agent_py, "a") as fh:
            fh.write(f"\n\n{MARKER}\ntry:\n    from . import _rsi_harness_adapter as _rsi_ha\n    _rsi_ha.apply()\n"
                     "except Exception as _e:\n    import logging as _lg\n"
                     "    _lg.getLogger('aide').warning('rsi harness adapter failed to apply: %s', _e)\n"
                     "# <<< rsi-loop harness adapter <<<\n")
    print(json.dumps({"installed": os.path.join(pkg, "_rsi_harness_adapter.py"), "adapter_sha256": adapter_sha()}))


if __name__ == "__main__":
    if sys.argv[1:] == ["install"]:
        install()
