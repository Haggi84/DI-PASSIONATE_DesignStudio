"""Runs inside FreeCAD: list the geometric bodies of a .FCStd with the facts needed to build
the layer stack (otter_llm/model.py does that part, outside FreeCAD).

Lengths are FreeCAD's internal unit, mm. Bounding boxes are taken from obj.Shape, the same
way the OTTER generators take them.
"""
import os
import sys

sys.path.insert(0, os.environ["OTTER_LLM_HEADLESS_DIR"])
from freecad_job import runJob

# datum features, sketches and annotations are construction help, not simulation geometry
SKIP_TYPE_PREFIXES = ("App::Origin", "App::Line", "App::Plane", "App::Point", "Sketcher::",
                      "Measure::", "Spreadsheet::", "App::DocumentObjectGroup", "App::Part",
                      "App::Annotation", "Mesh::")


def run(job, result):
    import FreeCAD

    doc = FreeCAD.openDocument(job["fcstd"])
    bodies, warnings = [], []
    viewDir = job.get("view_dir")  # STL per body for the web app's 3D overview

    for obj in doc.Objects:
        if obj.TypeId.startswith(SKIP_TYPE_PREFIXES):
            continue
        if obj.TypeId.startswith("App::Link"):
            warnings.append(f"{obj.Label}: App::Link is not read, OTTER exports the linked object itself")
            continue
        shape = getattr(obj, "Shape", None)
        if shape is None or shape.isNull() or (not shape.Faces and not shape.Edges):
            continue

        body = describeBody(obj, shape)
        if viewDir and body["kind"] != "wire":
            try:
                body["mesh"] = exportMesh(shape, viewDir, len(bodies))
            except Exception as error:
                warnings.append(f"{obj.Label}: no 3D view ({error})")
        bodies.append(body)

        parent = obj.getParentGeoFeatureGroup()
        if parent is not None and not parent.Placement.isIdentity():
            warnings.append(f"{obj.Label}: container '{parent.Label}' is moved or rotated, OTTER uses the "
                            f"object's own coordinates, so the solver sees it at a different place")

    result["file"] = os.path.basename(job["fcstd"])
    result["unit"] = "mm"
    result["bodies"] = bodies
    result["warnings"] = warnings


def exportMesh(shape, viewDir, index):
    """Coarse triangles of the shape (0.2 % of its size), enough to see it, as a binary STL."""
    import MeshPart

    deflection = max(shape.BoundBox.DiagonalLength * 0.002, 1e-7)
    mesh = MeshPart.meshFromShape(Shape=shape, LinearDeflection=deflection, AngularDeflection=0.35)
    name = f"{index:04d}.stl"
    mesh.write(os.path.join(viewDir, name))
    return name


def describeBody(obj, shape):
    bb = shape.BoundBox
    size = (bb.XLength, bb.YLength, bb.ZLength)
    tol = 1e-6 * max(max(size), 1e-9)  # extrusions of sketches are off by ~1e-7 relative

    if shape.Solids:
        kind = "solid"
    elif shape.Faces:
        kind = "sheet"
    else:
        kind = "wire"

    # a flat sheet's normal axis, "x" for a face in a y-z plane (how ports are usually drawn)
    flatAxis = None
    if kind == "sheet":
        flatAxes = [axis for axis, length in zip("xyz", size) if length <= tol]
        flatAxis = flatAxes[0] if len(flatAxes) == 1 else None

    bbVolume = size[0] * size[1] * size[2]
    isBox = kind == "solid" and len(shape.Faces) == 6 and bbVolume > 0 and abs(shape.Volume - bbVolume) <= 1e-6 * bbVolume

    # objects built from this one (extrusion base, boolean tool, ...); sketches attached to it and
    # the containers holding it are not users
    usedBy = [user.Label for user in obj.InList
              if hasattr(user, "Shape") and not user.TypeId.startswith(SKIP_TYPE_PREFIXES)
              and not user.hasExtension("App::GroupExtension")]

    color, transparency = None, 0
    view = obj.ViewObject
    if view is not None and hasattr(view, "ShapeColor"):
        color = [round(c, 4) for c in view.ShapeColor[:3]]
        transparency = getattr(view, "Transparency", 0)

    parent = obj.getParentGeoFeatureGroup()
    return {
        "name": obj.Name,
        "label": obj.Label,
        "type": obj.TypeId,
        "kind": kind,
        "bbox": [bb.XMin, bb.YMin, bb.ZMin, bb.XMax, bb.YMax, bb.ZMax],
        "volume": shape.Volume if kind == "solid" else 0.0,
        "area": shape.Area,
        "faces": len(shape.Faces),
        "is_box": isBox,
        "flat_axis": flatAxis,
        "used_by": usedBy,
        "visible": bool(view.Visibility) if view is not None else True,
        "color": color,
        "transparency": transparency,
        "container": parent.Label if parent is not None else None,
    }


runJob(run)
