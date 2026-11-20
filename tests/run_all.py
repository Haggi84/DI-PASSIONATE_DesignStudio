# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
Run every headless test:

    "<FreeCAD>\bin\freecadcmd.exe" tests\run_all.py

Exit code 0 when every check passed. The report is also written to
tests/results.log, because FreeCAD's own console output can interleave with
print() and make the console unreliable to read.
"""

import importlib
import os
import sys
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from _harness import TestResult  # noqa: E402

MODULES = sorted(f[:-3] for f in os.listdir(HERE) if f.startswith("test_") and f.endswith(".py"))


def main():
    results = []
    for name in MODULES:
        try:
            results += importlib.import_module(name).run()
        except Exception as exc:
            results.append(TestResult(f"{name}: MODULE CRASH", False, f"{exc}\n{traceback.format_exc()}"))
    skipped = [r for r in results if r.skipped]
    failed = [r for r in results if not r.passed]
    lines = [f"RESULTS: {len(results) - len(failed) - len(skipped)} passed, {len(failed)} failed, "
             f"{len(skipped)} skipped, {len(results)} total"]
    for r in results:
        if r.skipped:
            lines.append(f"[SKIP] {r.name} — {r.message}")
        else:
            lines.append(f"[{'PASS' if r.passed else 'FAIL'}] {r.name}")
            if not r.passed and r.message:
                lines += ["       " + l for l in r.message.splitlines()]
    lines.append(f"{len(failed)} check(s) FAILED." if failed else "All checks passed.")
    report = "\n".join(lines)
    with open(os.path.join(HERE, "results.log"), "w", encoding="utf-8") as fh:
        fh.write(report + "\n")
    print(report)
    return 1 if failed else 0


# freecadcmd imports this file as a module named after it, so a __main__
# guard would never fire.
sys.exit(main())
