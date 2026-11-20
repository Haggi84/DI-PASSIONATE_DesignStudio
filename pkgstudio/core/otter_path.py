# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
Where the vendored OTTER lives, and how it is put on the import path.

OTTER is two things: the FreeCAD add-on that writes the solver scripts for
openEMS, EMerge and Palace (thirdparty/otter/OTTER/addon) and otter_llm, the
plain-Python layer that builds a layer stack, settings and checks from a
model (thirdparty/otter/otter_llm). Both are copied in unchanged from
DI-PASSIONATE, so an update is a copy again, not a merge.

FreeCAD puts every Mod and Macro folder on sys.path, and a user who also
has OTTER as a macro, or DI-PASSIONATE with its own copy, has a second
`otter_llm` that would silently be imported instead. A copy from another
version writes different scripts for the same settings, which nobody would
notice. So ensure() puts this copy first and drops modules an earlier
import loaded from somewhere else.
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
VENDOR_DIR = os.path.join(ROOT, "thirdparty", "otter")
OTTER_DIR = os.path.join(VENDOR_DIR, "OTTER")          # the OTTER repo: add-on, docs, examples
ADDON_DIR = os.path.join(OTTER_DIR, "addon")

# Top-level modules of the vendored code. An entry in sys.modules under one of
# these names that was not loaded from VENDOR_DIR belongs to another copy.
_OWN_TOP_LEVEL = ("otter_llm", "OtterDialog", "utilsOtter", "utilsOtter3rdParty")


def _is_ours(module):
    path = getattr(module, "__file__", None) or ""
    if not path:
        path = next(iter(getattr(module, "__path__", []) or []), "")
    return os.path.realpath(path).startswith(os.path.realpath(VENDOR_DIR) + os.sep)


def foreign_modules():
    """Names of already imported OTTER modules that come from another copy."""
    found = []
    for name, module in list(sys.modules.items()):
        top = name.split(".", 1)[0]
        if top in _OWN_TOP_LEVEL and module is not None and not _is_ours(module):
            found.append(name)
    return sorted(found)


def ensure():
    """Make `import otter_llm` find the vendored copy, point it at the
    vendored add-on, and return the foreign modules that were dropped."""
    if not os.path.isfile(os.path.join(ADDON_DIR, "OtterDialog.py")):
        raise FileNotFoundError(
            f"The OTTER add-on is missing from {ADDON_DIR}. It ships with this "
            "workbench in thirdparty/otter; restore that folder.")
    dropped = foreign_modules()
    for name in dropped:
        del sys.modules[name]
    for path in (VENDOR_DIR, ADDON_DIR):
        while path in sys.path:
            sys.path.remove(path)
    # VENDOR_DIR first: otter_llm. ADDON_DIR after it: OtterDialog and
    # utilsOtter, which the add-on imports by their bare names.
    sys.path.insert(0, ADDON_DIR)
    sys.path.insert(0, VENDOR_DIR)
    # otter_llm.config looks the add-on up through this variable; set
    # unconditionally, so a leftover from another copy is not mixed in.
    os.environ["OTTER_ADDON_DIR"] = ADDON_DIR
    return dropped
