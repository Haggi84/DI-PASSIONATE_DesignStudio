# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
#
# FreeCAD runs this file with separate globals and locals, so it defines
# nothing itself: the workbench lives in pkgstudio.gui.workbench.
import FreeCADGui
from pkgstudio.gui.workbench import PackageStudioWorkbench

FreeCADGui.addWorkbench(PackageStudioWorkbench())
