# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
The EM setup of each open document, shared by the EM panel and the
assistant.

Both edit the same project: a material the assistant changes must appear in
the panel's table, and a solver picked in the panel is what the assistant
is told. So the project lives here, keyed by the package document, and
whoever changes it calls changed() to have every view redraw.
"""

import FreeCAD

_projects = {}
_listeners = []


def project_for(doc):
    """The EM project of *doc*, or None — also None once its model document was closed."""
    if doc is None:
        return None
    project = _projects.get(doc.Name)
    if project is not None and project.sim.doc.Name not in FreeCAD.listDocuments():
        _projects.pop(doc.Name, None)
        return None
    return project


def set_project(doc, project):
    old = _projects.get(doc.Name)
    if old is not None and old is not project:
        try:
            if old.sim.doc.Name in FreeCAD.listDocuments():
                FreeCAD.closeDocument(old.sim.doc.Name)
        except Exception:
            pass
    _projects[doc.Name] = project
    changed()


def add_listener(callback):
    if callback not in _listeners:
        _listeners.append(callback)


def remove_listener(callback):
    if callback in _listeners:
        _listeners.remove(callback)


def changed():
    for callback in list(_listeners):
        try:
            callback()
        except Exception as exc:
            FreeCAD.Console.PrintWarning(f"[PackageStudio] EM view refresh failed: {exc}\n")
