# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
Headless test harness. Run every test with:

    "<FreeCAD>\bin\freecadcmd.exe" tests\run_all.py

Each test_*.py module exposes run() -> [TestResult]. No pytest: the tests
need FreeCAD and OCCT, which live in freecadcmd's own interpreter.
"""

import os
import sys
import tempfile
import traceback

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)
TECH = os.path.join(REPO_ROOT, "technologies")


class TestResult:
    def __init__(self, name, passed, message="", skipped=False):
        self.name, self.passed, self.message, self.skipped = name, passed, message, skipped


class TestCase:
    """Named pass/fail checks under one prefix; never raises."""

    def __init__(self, prefix):
        self.prefix = prefix
        self.results = []

    def check(self, name, condition, message=""):
        ok = bool(condition)
        self.results.append(TestResult(f"{self.prefix}: {name}", ok, "" if ok else str(message)))
        return ok

    def near(self, name, got, want, tol=1e-9):
        return self.check(name, abs(got - want) <= tol, f"got {got!r}, want {want!r} (tol {tol})")

    def skip(self, name, reason):
        """Could not run here. Reported as SKIP, never counted as a pass."""
        self.results.append(TestResult(f"{self.prefix}: {name}", True, reason, skipped=True))

    def guard(self, name, fn):
        try:
            fn()
            return True
        except Exception as exc:
            self.results.append(TestResult(f"{self.prefix}: {name}", False,
                                           f"{exc}\n{traceback.format_exc()}"))
            return False


def tmpdir(prefix):
    return tempfile.mkdtemp(prefix=f"pkgstudio_{prefix}_")


def new_document(name):
    import FreeCAD
    if name in FreeCAD.listDocuments():
        FreeCAD.closeDocument(name)
    return FreeCAD.newDocument(name)
