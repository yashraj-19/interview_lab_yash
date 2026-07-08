"""Sandboxed candidate-code runner — real test-case execution for problem
scenarios (replaces the stdout:"" exitCode:0 stub).

Honestly labeled DEV-SANDBOX, not a hardened jail: the candidate's code runs
in a separate python process with `-I` (isolated: no site-packages, no env
site hooks, no cwd on sys.path), a hard wall-clock timeout, and no arguments
from the parent's environment. Good for a local lab; a hosted deployment
would swap this for a container/jail behind the same function signature.

The harness feeds each TestCase's input_args as kwargs, captures the result,
and compares against expected_output (sorting both sides for problems whose
answer is explicitly order-insensitive, e.g. two_sum). Anything the candidate
prints is captured separately so print-debugging still shows up in the UI.
"""
from __future__ import annotations

import json
import subprocess
import sys
from typing import Any

from .problem_spec import ProblemSpec

RUN_TIMEOUT_SECS = 3.0

# Runs inside the child process. Reads the harness spec from argv[1] (a JSON
# file written by the parent), execs the candidate code, calls the target
# function per case, and prints ONE json line with the results.
_HARNESS = r"""
import json, sys, io, contextlib, traceback
spec = json.load(open(sys.argv[1], encoding="utf-8"))
ns = {}
printed = io.StringIO()
results = []
try:
    with contextlib.redirect_stdout(printed):
        exec(compile(spec["code"], "<candidate>", "exec"), ns)
    fn = ns.get(spec["function"])
    if not callable(fn):
        raise NameError("function %r not found" % spec["function"])
    for case in spec["cases"]:
        try:
            with contextlib.redirect_stdout(printed):
                got = fn(**case["input"])
            expected = case["expected"]
            if spec.get("unordered") and isinstance(got, list) and isinstance(expected, list):
                ok = sorted(got) == sorted(expected)
            else:
                ok = got == expected
            results.append({"ok": ok, "got": repr(got), "expected": repr(expected),
                            "description": case.get("description", "")})
        except Exception:
            results.append({"ok": False, "got": "raised: " + traceback.format_exc(limit=1).strip().splitlines()[-1],
                            "expected": repr(case["expected"]), "description": case.get("description", "")})
    print(json.dumps({"status": "ok", "results": results, "printed": printed.getvalue()[-2000:]}))
except Exception:
    err = traceback.format_exc(limit=2)
    print(json.dumps({"status": "error", "error": err[-1500:], "printed": printed.getvalue()[-2000:]}))
"""


def run_candidate_code(code: str, problem: ProblemSpec, function_name: str) -> dict[str, Any]:
    """Execute candidate code against the problem's test cases.

    Returns a payload shaped for the `code.run` ledger event:
    ``{"stdout": str, "exitCode": int, "passed": int, "total": int,
       "results": [{ok, got, expected, description}...]}``.
    exitCode 0 = all cases passed; 1 = some failed / code errored; 2 = timeout.
    """
    import tempfile, os

    cases = [
        {"input": c.input_args, "expected": c.expected_output, "description": c.description}
        for c in problem.test_cases
    ]
    spec = {"code": code, "function": function_name, "cases": cases,
            "unordered": problem.unordered_result}

    spec_path = None
    try:
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as f:
            json.dump(spec, f)
            spec_path = f.name
        proc = subprocess.run(
            [sys.executable, "-I", "-c", _HARNESS, spec_path],
            capture_output=True, text=True, timeout=RUN_TIMEOUT_SECS,
        )
    except subprocess.TimeoutExpired:
        return {
            "stdout": f"Timed out after {RUN_TIMEOUT_SECS:.0f}s — likely an infinite loop.",
            "exitCode": 2, "passed": 0, "total": len(cases), "results": [],
        }
    finally:
        if spec_path:
            try:
                os.unlink(spec_path)
            except OSError:
                pass

    try:
        out = json.loads(proc.stdout.strip().splitlines()[-1])
    except Exception:
        return {
            "stdout": (proc.stderr or proc.stdout or "no output")[-1500:],
            "exitCode": 1, "passed": 0, "total": len(cases), "results": [],
        }

    if out.get("status") != "ok":
        return {
            "stdout": (out.get("error") or "error") + ("\n" + out["printed"] if out.get("printed") else ""),
            "exitCode": 1, "passed": 0, "total": len(cases), "results": [],
        }

    results = out.get("results", [])
    passed = sum(1 for r in results if r.get("ok"))
    lines = [f"{passed}/{len(results)} test cases passed."]
    for r in results:
        mark = "PASS" if r.get("ok") else "FAIL"
        desc = f" — {r['description']}" if r.get("description") else ""
        lines.append(f"  [{mark}]{desc}: got {r['got']}, expected {r['expected']}")
    if out.get("printed"):
        lines.append("--- your output ---")
        lines.append(out["printed"].rstrip())
    return {
        "stdout": "\n".join(lines),
        "exitCode": 0 if passed == len(results) and results else 1,
        "passed": passed, "total": len(results), "results": results,
    }


__all__ = ["run_candidate_code", "RUN_TIMEOUT_SECS"]
