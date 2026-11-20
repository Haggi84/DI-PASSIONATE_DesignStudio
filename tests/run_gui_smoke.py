# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
Launch the GUI smoke test (tests/gui/smoke_macro.py) in the full FreeCAD:

    "<FreeCAD>\\bin\\python.exe" tests\\run_gui_smoke.py

A FreeCAD window opens for under a minute and closes itself. It runs with a
throwaway user folder, so your preferences are untouched. Set
PS_FREECAD_EXE for a FreeCAD elsewhere. Exit code 0 when every check passed.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))


def freecad():
    for c in (os.environ.get("PS_FREECAD_EXE", ""),
              os.path.join(os.path.dirname(sys.executable), "FreeCAD.exe"),
              r"C:\Program Files\FreeCAD 1.1\bin\FreeCAD.exe",
              shutil.which("FreeCAD") or "", shutil.which("freecad") or ""):
        if c and os.path.isfile(c):
            return c
    return None


def main():
    exe = freecad()
    if not exe:
        print("FreeCAD not found — set PS_FREECAD_EXE.")
        return 2
    home = tempfile.mkdtemp(prefix="pd_gui_smoke_")
    result = os.path.join(home, "result.json")
    env = dict(os.environ, FREECAD_USER_HOME=home, PS_SMOKE_RESULT=result,
               PS_SMOKE_SHOT=os.environ.get("PS_SMOKE_SHOT", ""))
    try:
        subprocess.run([exe, os.path.join(HERE, "gui", "smoke_macro.py")], env=env, timeout=600)
    except subprocess.TimeoutExpired:
        print("FreeCAD did not finish within 600 s.")
        return 1
    if not os.path.isfile(result):
        print("No result written — the macro did not run.")
        return 1
    data = json.load(open(result, encoding="utf-8"))
    for c in data["checks"]:
        print(f"[{'PASS' if c['passed'] else 'FAIL'}] {c['name']}")
        if not c["passed"] and c["detail"]:
            print("       " + c["detail"].replace("\n", "\n       "))
    ok = all(c["passed"] for c in data["checks"]) and data["checks"]
    print(f"{sum(c['passed'] for c in data['checks'])} passed, "
          f"{sum(not c['passed'] for c in data['checks'])} failed")
    shutil.rmtree(home, ignore_errors=True)
    return 0 if ok else 1


sys.exit(main())
