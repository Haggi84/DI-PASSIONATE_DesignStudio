#   author: Lubomir Jagos
#
#
import os
from PySide import QtGui, QtCore, QtWidgets
import numpy as np
import re
import math

from utilsOtter.GlobalFunctions.GlobalFunctions import _bool, _r
from utilsOtter.SettingsItem.SettingsItem import SettingsItem
from utilsOtter.GuiHelpers.GuiHelpers import GuiHelpers
from utilsOtter.GuiHelpers.FactoryCadInterface import FactoryCadInterface

try:
	import FreeCAD
except:
    pass

class SimpleBoundBox:
    """
    Minimal bounding box with same attributes as FreeCAD BoundBox, used when box is modified (ie. port collapsed to plane).
    """
    def __init__(self, xmin, ymin, zmin, xmax, ymax, zmax):
        self.XMin, self.YMin, self.ZMin = xmin, ymin, zmin
        self.XMax, self.YMax, self.ZMax = xmax, ymax, zmax

    @property
    def XLength(self):
        return self.XMax - self.XMin

    @property
    def YLength(self):
        return self.YMax - self.YMin

    @property
    def ZLength(self):
        return self.ZMax - self.ZMin

    def isValid(self):
        return True

    @staticmethod
    def fromBoundBox(bb):
        return SimpleBoundBox(bb.XMin, bb.YMin, bb.ZMin, bb.XMax, bb.YMax, bb.ZMax)

    def collapsed(self, axisIndex, coord):
        """
        Returns copy of box with zero thickness at coord in axis (0=x, 1=y, 2=z).
        """
        bbMin = [self.XMin, self.YMin, self.ZMin]
        bbMax = [self.XMax, self.YMax, self.ZMax]
        bbMin[axisIndex] = bbMax[axisIndex] = coord
        return SimpleBoundBox(*bbMin, *bbMax)

class CommonScriptLinesGenerator:

    #
    #   constructor, get access to form GUI
    #
    def __init__(self, form, statusBar = None):
        self.form = form
        self.statusBar = statusBar

        self.internalPortIndexNamesList = {}
        self.internalMaterialIndexNamesList = {}
        self.internalNF2FFIndexNamesList = {}

        #
        # GUI helpers function like display message box and so
        #
        self.guiHelpers = GuiHelpers(self.form, statusBar = self.statusBar)
        self.cadHelpers = FactoryCadInterface.createHelper()

        #
        # Warnings about settings which are known to give wrong simulation results, they are written at beginning
        # of generated script and displayed to user when script is generated.
        #
        self.exportWarnings = []

    def addExportWarning(self, msg):
        print("EXPORT WARNING: " + msg)
        if not msg in self.exportWarnings:
            self.exportWarnings.append(msg)

    def getExportWarningsScriptLines(self):
        if len(self.exportWarnings) == 0:
            return ""

        genScript = "#\n"
        genScript += "# EXPORT WARNINGS (settings known to give wrong or slow results, check them):\n"
        for msg in self.exportWarnings:
            genScript += f"#   - {msg}\n"
        genScript += "#\n"
        return genScript

    def getExportWarningsMessage(self):
        if len(self.exportWarnings) == 0:
            return ""
        return "\n\nWARNINGS:\n" + "\n".join([f"- {msg}" for msg in self.exportWarnings])

    def getModelObjectBoundBox(self, objectLabel):
        """
        Returns FreeCAD BoundBox of object with given label or None if object has no valid shape.
        """
        for obj in self.cadHelpers.getObjects():
            if obj.Label == objectLabel:
                try:
                    bb = obj.Shape.BoundBox
                    if bb.isValid():
                        return bb
                except:
                    pass
        return None

    def isAxisAlignedBox(self, freeCadObj):
        """
        True if object is one solid which completely fills its bounding box, so it can be modeled as box.
        """
        try:
            shape = freeCadObj.Shape
            if shape.isNull() or len(shape.Solids) != 1:
                return False
            bb = shape.BoundBox
            bbVolume = bb.XLength * bb.YLength * bb.ZLength
            return bbVolume > 0 and abs(shape.Volume - bbVolume) <= 1e-6 * bbVolume
        except:
            return False

    def getModelPlanesForAxis(self, axisIndex, excludeLabel="", tol=1e-6):
        """
        Returns list of all bounding box planes of objects in model for given axis (0=x, 1=y, 2=z) in FreeCAD units.
        Object excludeLabel and objects with same bounding box as it (group, body or link containing it) are skipped.
        """
        excludeBB = self.getModelObjectBoundBox(excludeLabel) if excludeLabel != "" else None
        def sameBB(a, b):
            return b is not None and max(abs(a.XMin - b.XMin), abs(a.YMin - b.YMin), abs(a.ZMin - b.ZMin),
                                         abs(a.XMax - b.XMax), abs(a.YMax - b.YMax), abs(a.ZMax - b.ZMax)) <= tol

        planes = []
        for obj in self.cadHelpers.getObjects():
            if obj.Label == excludeLabel:
                continue
            try:
                bb = obj.Shape.BoundBox
                if not bb.isValid() or sameBB(bb, excludeBB):
                    continue
            except:
                continue
            planes += [[bb.XMin, bb.YMin, bb.ZMin][axisIndex], [bb.XMax, bb.YMax, bb.ZMax][axisIndex]]
        return planes

    def getPlaneTouchingModel(self, axisIndex, lo, hi, excludeLabel="", tol=1e-6):
        """
        From two faces lo, hi (FreeCAD units) of thin object returns the one which lays on plane of some other object
        in model (the face where it's connected), prefers hi, if none touches returns middle.
        """
        planes = np.array(self.getModelPlanesForAxis(axisIndex, excludeLabel, tol))
        touches = lambda v: planes.size > 0 and np.min(np.abs(planes - v)) <= tol
        if touches(hi):
            return hi
        if touches(lo):
            return lo
        return 0.5 * (lo + hi)

    def getThinSheetPlane(self, freeCadObj, maxRelativeThickness=0.1):
        """
        For thin 3D object (thickness < maxRelativeThickness * smaller other size) returns (axisIndex, planeCoord)
        of face to use as zero-thickness sheet, otherwise None.
        """
        bb = self.getModelObjectBoundBox(freeCadObj.Label)
        if bb is None:
            return None
        dims = [bb.XLength, bb.YLength, bb.ZLength]
        axis = int(np.argmin(dims))
        others = [dims[k] for k in range(3) if k != axis]
        if dims[axis] <= 0 or dims[axis] > maxRelativeThickness * min(others):
            return None
        lo = [bb.XMin, bb.YMin, bb.ZMin][axis]
        hi = [bb.XMax, bb.YMax, bb.ZMax][axis]
        return axis, self.getPlaneTouchingModel(axis, lo, hi, excludeLabel=freeCadObj.Label)

    def getUnitLengthFromUI_m(self):
        unitStr = self.form.simParamsDeltaUnitList.currentText()
        return SettingsItem.getUnitsAsNumber(unitStr)

    def getFreeCADUnitLength_m(self):
        # # FreeCAD uses mm internally, so getFreeCADUnitLength_m() should always return 0.001.
        # # Below is one way to retrieve this value from schemaTranslate() without implying it.
        # [qtyStr, standardUnitsPerTargetUnit, targetUnitStr] = App.Units.schemaTranslate( App.Units.Quantity("1.0 m"), App.Units.Scheme.SI2 )
        # return 1.0 / standardUnitsPerTargetUnit # standard unit is mm : return 1.0 / 1000 [m]
        return 0.001

    def getFreeCADInternalUnitLengthStr(self):
        try:
            [qtyStr, standardUnitsPerTargetUnit, targetUnitStr] = FreeCAD.Units.schemaTranslate( FreeCAD.Units.Quantity("1.0 m"), FreeCAD.Units.Scheme.SI2 )
        except:
            [qtyStr, standardUnitsPerTargetUnit, targetUnitStr] = FreeCAD.Units.schemaTranslate( FreeCAD.Units.Quantity("1.0 m"), FreeCAD.Units.Scheme.MmMin )

        return targetUnitStr

    def getItemsByClassName(self):
        categoryCount = self.form.objectAssignmentRightTreeWidget.invisibleRootItem().childCount()
        categoryNodes = [self.form.objectAssignmentRightTreeWidget.topLevelItem(k) for k in range(categoryCount)]
        itemsByClassName = {}
        for m, categoryNode in enumerate(categoryNodes):  # for each category
            for k in range(categoryNode.childCount()):  # for each child item in a given category
                item = categoryNode.child(k)
                itemData = item.data(0, QtCore.Qt.UserRole)
                if not itemData:
                    continue
                itemClassName = itemData.__class__.__name__
                if not (itemClassName in itemsByClassName):
                    itemsByClassName[itemClassName] = [[item, itemData]]
                else:
                    itemsByClassName[itemClassName].append([item, itemData])

        print("generateOpenEMSScript: Item classes found = " + ", ".join(itemsByClassName.keys()))

        return itemsByClassName

    def checkLumpedPortConductorOverlap(self, portObj, minRelativeOverlap=0.01, minConductivity=1e3):
        """
        Lumped port must span just the gap between conductors (ie. ground top -> line bottom). Port drawn through metal (ie. from ground
        bottom to line top) has wrong length, solver which takes port size as voltage path (EMerge) gives wrong impedance. Adds export
        warning for each conductor overlapping more than minRelativeOverlap of port area (planar port) or volume (3D port).
        """
        def _num(value):
            try:
                return float(value)
            except:
                return 0.0

        try:
            portShape = portObj.Shape
            isPlanar = abs(portShape.Volume) <= 1e-12
            portSize = portShape.Area if isPlanar else portShape.Volume
        except:
            return
        if portSize <= 0:
            return

        fcObjects = {obj.Label: obj for obj in self.cadHelpers.getObjects()}
        for [item, currSetting] in self.getItemsByClassName().get("MaterialSettingsItem", []):
            constants = getattr(currSetting, 'constants', {})
            isConductor = currSetting.type == 'metal' or \
                          (currSetting.type == 'userdefined' and max(_num(constants.get('kappa', 0)), _num(constants.get('sigma', 0))) >= minConductivity)
            if not isConductor:
                continue
            for k in range(item.childCount()):
                conductorObj = fcObjects.get(item.child(k).text(0), None)
                if conductorObj is None or conductorObj is portObj:
                    continue
                try:
                    if len(conductorObj.Shape.Solids) == 0 or not portShape.BoundBox.intersect(conductorObj.Shape.BoundBox):
                        continue
                    common = portShape.common(conductorObj.Shape)
                    overlap = (common.Area if isPlanar else common.Volume) / portSize
                except:
                    continue
                if overlap > minRelativeOverlap:
                    self.addExportWarning(f"port '{portObj.Label}' overlaps conductor '{conductorObj.Label}' ({100 * overlap:.0f} % of port), " +
                                          "lumped port must span just the gap between conductors (ie. ground top to line bottom), otherwise port length and impedance are wrong")

    #
    #	Returns object priority
    #		priorityItemName - string which identifies item by its text in priority tree view widget
    #
    def getItemPriority(self, priorityItemName):
        #
        #	priority is read from tree view
        #
        priorityItemValue = 42
        itemsCount = self.form.objectAssignmentPriorityTreeView.topLevelItemCount()
        for k in range(itemsCount):
            priorityItem = self.form.objectAssignmentPriorityTreeView.topLevelItem(k)
            if priorityItem.text(0) == priorityItemName:     # exact, "patch" must not match "patch_extrude"
                #
                #	THIS IS MY FORMULA TO HAVE AT LEAST TWO 0 AT END AND NOT HAVE PRIORITY INDEX 0 BUT START AT 100 AT LEAST!
                #		ATTENTION: higher number means higher priority so fromual is: (1001 - k)     ...to get item at top of tree view with highest priority numbers!
                #
                priorityItemValue = (100 - k) * 100
                break  # this will break loop SO JUST ONE ITEM FROM PRIORITY LIST IS DELETED

        return priorityItemValue

    #
    #   Returns current FreeCAD file:
    #       - absolute directory
    #       - name without extension
    #
    def getCurrDir(self):
        programname = os.path.basename(self.cadHelpers.getCurrDocumentFileName())
        programDir = os.path.dirname(self.cadHelpers.getCurrDocumentFileName())
        programbase, ext = os.path.splitext(programname)  # extract basename and ext from filename
        return [programDir, programbase]

    #
    #   Creates output dir in current FreeCAD file directory if not exists.
    #       outputDir - absolute path to directory where folder with simulation files should be created
    #
    def createOuputDir(self, outputDir):
        programname = os.path.basename(self.cadHelpers.getCurrDocumentFileName())     # FreeCAD filename with extension
        programdir = os.path.dirname(self.cadHelpers.getCurrDocumentFileName())       # FreeCAD file directory
        programbase, ext = os.path.splitext(programname)                # FreeCAD filename without extension

        #
        #   Simulation files will be saved in folder named based on FreeCAD filename and suffix _openEMS_simulation
        #       If parameter outputDir is not set this folder will be generated in the same folder as FreeCAD file.
        #       If outputDir is set folder with simulation folder with files is genenrated next to .ini file
        #
        if outputDir is None or outputDir == False:
            absoluteOutputDir = f"{programdir}/{programbase}_openEMS_simulation"
        else:
            absoluteOutputDir = outputDir

        if not os.path.exists(absoluteOutputDir):
            os.makedirs(absoluteOutputDir)
            print(f"Created directory for simulation: {outputDir}")

        return absoluteOutputDir

    def reportFreeCADItemSettings(self, items):
        # "FreeCAD item detection everywhere in Main Tree!!! need to get rid this, now it's tolerated during development!"
        if not items:
            return

        for [item, currSetting] in items:
            #	GET PARENT NODE DATATYPE
            print("#")
            print("#FREECAD OBJ.")
            if (str(item.parent().text(0)) == "Grid"):
                print("name: Grid Default")
                print("type: FreeCADSettingsItem")
                pass
            elif (str(item.parent().text(0)) == "Ports"):
                print("name: Port Default")
                print("type: FreeCADSettingsItem")
                pass
            elif (str(item.parent().text(0)) == "Excitation"):
                print("name: Excitation Default")
                print("type: FreeCADSettingsItem")
                pass
            elif (str(item.parent().text(0)) == "Materials"):
                print("name: Material Default")
                print("type: FreeCADSettingsItem")
                pass
            else:
                print("Parent of FreeCADSettingItem UNKNOWN")
                pass

    def getModelCoordsType(self):
        """
        Returns current coordinate system, as there can be just rectangular or just cylindrical for all grid items it's enough to look at first grid item.
        :return: string (rectangular, cylindrical)
        """
        if self.form.gridSettingsTreeView.topLevelItemCount() > 0:
            coordsType = self.form.gridSettingsTreeView.topLevelItem(0).data(0, QtCore.Qt.UserRole).coordsType
        else:
            coordsType = "rectangular"
        return coordsType

    def getSketchPointsForConductingSheet(self, freeCadObj):
        """
        Generate points for conducting sheet area. Conducting sheet must lay in XY,XZ or YZ plane.
        :return: list of vertices which creates area of conducting sheet.
        """

        #
        #   Sketch is added as polygon into conducting sheet material
        #
        normDir = ""
        elevation = 0.0
        points = [[],[]]
        bbCoords = freeCadObj.Shape.BoundBox

        if (_r(bbCoords.XMin) == _r(bbCoords.XMax)):
            normDir = "x"
            elevation = _r(bbCoords.XMin)
        elif (_r(bbCoords.YMin) == _r(bbCoords.YMax)):
            normDir = "y"
            elevation = _r(bbCoords.YMin)
        elif (_r(bbCoords.ZMin) == _r(bbCoords.ZMax)):
            normDir = "z"
            elevation = _r(bbCoords.ZMin)
        else:
            normDir = "ERROR: sketch not lay in coordinate plane, conducting sheet polygon must lay in XY, XZ or YZ plane"

        """
        for geometryObj in freeCadObj.Geometry:
            if (str(type(geometryObj)).find("LineSegment") > -1):
                points[0].append(_r(geometryObj.StartPoint.x))
                points[1].append(_r(geometryObj.StartPoint.y))
        """

        if normDir == 'x':
            for v in freeCadObj.Shape.OrderedVertexes:
                points[0].append(_r(v.Y))
                points[1].append(_r(v.Z))
            points[0].append(_r(freeCadObj.Shape.OrderedVertexes[0].Y))
            points[1].append(_r(freeCadObj.Shape.OrderedVertexes[0].Z))
        elif normDir == 'y':
            for v in freeCadObj.Shape.OrderedVertexes:
                points[0].append(_r(v.X))
                points[1].append(_r(v.Z))
            points[0].append(_r(freeCadObj.Shape.OrderedVertexes[0].X))
            points[1].append(_r(freeCadObj.Shape.OrderedVertexes[0].Z))
        elif normDir == 'z':
            for v in freeCadObj.Shape.OrderedVertexes:
                points[0].append(_r(v.X))
                points[1].append(_r(v.Y))
            points[0].append(_r(freeCadObj.Shape.OrderedVertexes[0].X))
            points[1].append(_r(freeCadObj.Shape.OrderedVertexes[0].Y))

        return normDir, elevation, points

    def getPlanarFacePolygons(self, shape, axisIndex, coord, tol=1e-6):
        """
        Outlines of shape faces laying in plane axisIndex = coord (FreeCAD units) as [[u values], [v values]], closed (first point repeated),
        in-plane coordinates are (y, z) for x plane, (x, z) for y plane, (x, y) for z plane. Points follow the outer wire (face.Vertexes
        are not in outline order, polygon from them can cross itself), curved edges are discretized. Holes (inner wires) are not returned.
        """
        inPlane = [k for k in range(3) if k != axisIndex]
        polygons = []
        for face in shape.Faces:
            fbb = face.BoundBox
            lo = [fbb.XMin, fbb.YMin, fbb.ZMin][axisIndex]
            hi = [fbb.XMax, fbb.YMax, fbb.ZMax][axisIndex]
            if hi - lo > tol or abs(lo - coord) > tol:
                continue
            wire = face.OuterWire
            deflection = max(fbb.DiagonalLength * 1e-3, 1e-9)
            vertices = wire.discretize(Deflection=deflection)
            if len(vertices) > 1 and (vertices[0] - vertices[-1]).Length <= tol:
                vertices = vertices[:-1]
            if len(vertices) < 3:
                continue
            points = [[_r([v.x, v.y, v.z][k]) for v in vertices] for k in inPlane]
            points[0].append(points[0][0])
            points[1].append(points[1][0])
            polygons.append(points)
        return polygons

    def getFacePointsForConductingSheet(self, freeCadObj):
        """
        Planar object as conducting sheet polygons: (normal direction 'x'|'y'|'z', elevation, [[u values], [v values]] per face),
        normal direction is "" when object is not planar or has no faces.
        """
        normDir = ""
        elevation = ""
        facesList = []

        bbCoords = freeCadObj.Shape.BoundBox
        if len(freeCadObj.Shape.Faces) > 0:
            for axisIndex, (lo, hi) in enumerate([(bbCoords.XMin, bbCoords.XMax), (bbCoords.YMin, bbCoords.YMax), (bbCoords.ZMin, bbCoords.ZMax)]):
                if _r(lo) == _r(hi):
                    normDir = "xyz"[axisIndex]
                    elevation = lo
                    facesList = self.getPlanarFacePolygons(freeCadObj.Shape, axisIndex, lo)
                    break

        return normDir, elevation, facesList
