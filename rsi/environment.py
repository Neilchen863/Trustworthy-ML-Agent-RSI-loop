"""Read-only checks for task/environment contracts and external CRC prerequisites.

This checks declarations and file presence, not container execution or label contents.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
from pathlib import Path


def check_task(task_path: Path, metadata_only=False, environ=None, which=None) -> dict:
    env = os.environ if environ is None else environ
    find = shutil.which if which is None else which
    checks = []

    def record(name, ok, detail):
        checks.append({"name": name, "ok": bool(ok), "detail": detail})

    def read(path):
        data = json.loads(path.read_text())
        if not isinstance(data, dict):
            raise ValueError(f"{path}: expected a JSON object")
        return data

    try:
        task = read(task_path / "task.json")
        ep = task_path / task["environment"]
        environment = read(ep)
        evaluation = read(task_path / task["evaluation"])
        sp = ep.parent / environment["shared_environment"]
        shared = read(sp)
        record("metadata", True, "task, environment, shared environment and submission contract parsed")
        record("schema", all(d.get("schema_version") == 1 for d in (environment, evaluation, shared)),
               "supported contract schema_version is 1")
        record("competition", evaluation["grader"]["competition_id"] == task["competition_id"],
               "grader competition must match task")
        record("prediction", evaluation["prediction_column"] == task["label_column"]
               and task["label_column"] in evaluation["columns"], "prediction column must match task label")
        record("metric", evaluation["metric"] == task["metric"]["name"]
               and evaluation["direction"] == task["metric"]["direction"], "metric must match task")
        record("public-source", evaluation["row_alignment"]["source"] in environment["public_files"],
               "submission rows must refer to a declared public input")
        for name in ("prepare_script", "check_script"):
            p = ep.parent / environment[name]
            record(name, p.is_file(), str(p))
        builder = sp.parent / shared["overlay"]["build_script"]
        record("overlay-builder", builder.is_file(), str(builder))
        if not metadata_only:
            root = env.get(shared["external_runner"]["root_env"])
            record("runner-root", bool(root) and Path(root).expanduser().is_dir(),
                   "set MLEBENCH_AIDE_ROOT to the external research checkout")
            if root:
                for rel in shared["external_runner"]["required_files"]:
                    record("runner:" + rel, (Path(root).expanduser() / rel).is_file(), rel)
            for name in (shared["image"]["path_env"], shared["overlay"]["path_env"]):
                value = env.get(name)
                record(name, bool(value) and Path(value).expanduser().is_file(),
                       f"{name} must name an existing file; compatibility is not verified here")
            data = env.get(environment["data_root_env"])
            record("data-root", bool(data) and Path(data).expanduser().is_dir(), "set DATA_DIR")
            if data:
                prepared = Path(data).expanduser() / environment["prepared_directory"]
                for name in environment["public_files"]:
                    record("public:" + name, (prepared / "public" / name).is_file(), name)
                record("private-directory", (prepared / environment["private_directory"]).is_dir(),
                       "checked directory presence only; no private files read")
            for cmd in shared["commands"]:
                record("command:" + cmd, bool(find(cmd)), "must be available after loading cluster modules")
    except (OSError, ValueError, KeyError, TypeError) as exc:
        record("contract-error", False, str(exc))
    return {"task": task_path.name, "scope": "metadata" if metadata_only else "external-prerequisites",
            "ok": all(c["ok"] for c in checks), "checks": checks,
            "not_checked": ["container imports/execution", "external version compatibility",
                            "private label contents", "dataset and image hashes", "credentials"]}


def main(task_path: Path, argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--metadata-only", action="store_true", help="check local contracts without CRC/data")
    args = parser.parse_args(argv)
    result = check_task(task_path, args.metadata_only)
    print(json.dumps(result, indent=2))
    return 0 if result["ok"] else 1
