# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""The Package Studio workbench: one toolbar in the order the work goes, one menu."""

import FreeCADGui

from . import commands


class PackageStudioWorkbench(FreeCADGui.Workbench):
    MenuText = "Package Studio"
    ToolTip = ("Chip packaging assemblies: GDS chips, housings, boards, bond wires, ports and materials, "
               "to a ready-to-run EM simulation")
    Icon = commands.icon("workbench")

    def Initialize(self):
        names, menu_only = commands.register()
        system_names = commands.register_system()
        flow_names = commands.register_flow()
        self.appendToolbar("Package Studio", names)
        self.appendToolbar("Package Studio — Design flow", flow_names)
        self.appendToolbar("Package Studio — MCM & System", system_names)
        self.appendMenu("Package Studio", names + ["Separator"] + flow_names + ["Separator"] + system_names + ["Separator"] + menu_only)
        # offsets typed in the property editor move the object, wires follow
        from ..app import moving
        moving.install_observer()

    def Activated(self):
        # Chip layers are drawn as large meshes. Without OpenGL vertex buffers
        # FreeCAD redraws ALL_LNA's 2.3 million triangles at 505 ms a frame;
        # with them, at 37 ms. It is FreeCAD's own setting, for every
        # workbench, so it is pointed out rather than changed.
        import FreeCAD
        view = FreeCAD.ParamGet("User parameter:BaseApp/Preferences/View")
        if not view.GetBool("UseVBO", False) and not getattr(PackageStudioWorkbench, "_vbo_hint", False):
            PackageStudioWorkbench._vbo_hint = True
            FreeCAD.Console.PrintWarning(
                "[PackageStudio] Large chips turn smoothly only with OpenGL vertex buffers: "
                "Edit > Preferences > Display > 3D View > \"Use OpenGL VBO (Vertex Buffer Object)\" "
                "(14x faster on a 2.3-million-triangle chip).\n")

    def Deactivated(self):
        pass

    def GetClassName(self):
        return "Gui::PythonWorkbench"
