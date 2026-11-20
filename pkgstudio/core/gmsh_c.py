# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
The few calls into Gmsh's C API that the EM mesher needs, through ctypes.

FreeCAD ships Gmsh as gmsh.dll (gmsh.exe is a small launcher for it), but
not its Python module. A .geo script cannot do what a conformal EM mesh
needs: after fragmenting, find which faces of which conductor touch which
dielectric, so that each gets its own boundary condition. The API can, so
it is called directly — nothing to install, and it is always the Gmsh the
user's FreeCAD came with.

Only plain Python and ctypes here: this runs in a separate process (see
core/palace.py), so a crash in Gmsh cannot take FreeCAD with it.
"""

import ctypes
import os
import sys
from ctypes import POINTER, byref, c_char_p, c_double, c_int, c_size_t

_lib = None


def library_path(home=None):
    """gmsh.dll (or libgmsh.so) next to FreeCAD's gmsh executable."""
    names = ("gmsh.dll",) if os.name == "nt" else ("libgmsh.so", "libgmsh.so.4.15", "libgmsh.dylib")
    dirs = [home] if home else []
    dirs += [os.path.dirname(sys.executable), os.environ.get("PKGDESIGNER_GMSH_DIR", "")]
    for d in dirs:
        for n in names:
            p = os.path.join(d or "", n)
            if d and os.path.isfile(p):
                return p
    return None


def load(path=None):
    global _lib
    if _lib is None:
        path = path or library_path()
        if not path:
            raise RuntimeError("Gmsh's library (gmsh.dll) was not found next to FreeCAD's python.")
        if os.name == "nt":
            os.add_dll_directory(os.path.dirname(path))
        _lib = ctypes.CDLL(path)
    return _lib


class GmshError(RuntimeError):
    pass


def _call(name, *args):
    ierr = c_int()
    fn = getattr(load(), name)
    out = fn(*args, byref(ierr))
    if ierr.value != 0:
        msg = c_char_p()
        e2 = c_int()
        try:
            load().gmshLoggerGetLastError(byref(msg), byref(e2))
            text = msg.value.decode(errors="replace") if msg.value else ""
        except Exception:
            text = ""
        raise GmshError(f"{name} failed ({ierr.value}): {text}")
    return out


def _ints(values):
    arr = (c_int * len(values))(*values)
    return arr, c_size_t(len(values))


def _dimtags(pairs):
    return _ints([v for dt in pairs for v in dt])


def _take_ints(ptr, n):
    out = [ptr[i] for i in range(n.value)]
    if n.value:
        load().gmshFree(ptr)
    return out


def _pairs(flat):
    return [(flat[i], flat[i + 1]) for i in range(0, len(flat), 2)]


# ── session ─────────────────────────────────────────────────────────────────

def initialize():

    _call("gmshInitialize", c_int(0), None, c_int(0), c_int(0))


def finalize():
    _call("gmshFinalize")


def set_number(option, value):
    _call("gmshOptionSetNumber", option.encode(), c_double(value))


def add_model(name):
    _call("gmshModelAdd", name.encode())


def write(path):
    _call("gmshWrite", os.fsencode(path))


# ── geometry ────────────────────────────────────────────────────────────────

def import_shapes(path, highest_dim_only=True):
    out, n = POINTER(c_int)(), c_size_t()
    _call("gmshModelOccImportShapes", os.fsencode(path), byref(out), byref(n),
          c_int(1 if highest_dim_only else 0), b"")
    return _pairs(_take_ints(out, n))


def fragment(objects, tools):
    """(out dimtags, [dimtags each input became]) — inputs are *objects* then *tools*."""
    o, on = _dimtags(objects)
    t, tn = _dimtags(tools)
    out, n = POINTER(c_int)(), c_size_t()
    mp, mpn, mpnn = POINTER(POINTER(c_int))(), POINTER(c_size_t)(), c_size_t()
    _call("gmshModelOccFragment", o, on, t, tn, byref(out), byref(n),
          byref(mp), byref(mpn), byref(mpnn), c_int(-1), c_int(1), c_int(1))
    result = _pairs(_take_ints(out, n))
    maps = []
    for i in range(mpnn.value):
        maps.append(_pairs([mp[i][j] for j in range(mpn[i])]))
        if mpn[i]:
            load().gmshFree(mp[i])
    if mpnn.value:
        load().gmshFree(mp)
        load().gmshFree(mpn)
    return result, maps


def synchronize():
    _call("gmshModelOccSynchronize")


def remove_entities(dimtags, recursive=False):
    d, n = _dimtags(dimtags)
    _call("gmshModelRemoveEntities", d, n, c_int(1 if recursive else 0))


def entities(dim=-1):
    out, n = POINTER(c_int)(), c_size_t()
    _call("gmshModelGetEntities", byref(out), byref(n), c_int(dim))
    return _pairs(_take_ints(out, n))


def boundary(dimtags, combined=True, oriented=False, recursive=False):
    d, n = _dimtags(dimtags)
    out, on = POINTER(c_int)(), c_size_t()
    _call("gmshModelGetBoundary", d, n, byref(out), byref(on), c_int(int(combined)),
          c_int(int(oriented)), c_int(int(recursive)))
    return [(dim, abs(tag)) for dim, tag in _pairs(_take_ints(out, on))]


def adjacencies(dim, tag):
    """(upward tags, downward tags)."""
    up, upn, down, downn = POINTER(c_int)(), c_size_t(), POINTER(c_int)(), c_size_t()
    _call("gmshModelGetAdjacencies", c_int(dim), c_int(tag), byref(up), byref(upn),
          byref(down), byref(downn))
    return _take_ints(up, upn), _take_ints(down, downn)


def bounding_box(dim, tag):
    v = [c_double() for _ in range(6)]
    _call("gmshModelGetBoundingBox", c_int(dim), c_int(tag), *[byref(x) for x in v])
    return tuple(x.value for x in v)


def add_physical_group(dim, tags, name, tag=-1):
    load().gmshModelAddPhysicalGroup.restype = c_int
    t, n = _ints(tags)
    return _call("gmshModelAddPhysicalGroup", c_int(dim), t, n, c_int(tag), name.encode())


# ── mesh ────────────────────────────────────────────────────────────────────

def set_size(dimtags, size):
    d, n = _dimtags(dimtags)
    _call("gmshModelMeshSetSize", d, n, c_double(size))


def generate(dim=3):
    _call("gmshModelMeshGenerate", c_int(dim))


def clear_mesh():
    _call("gmshModelMeshClear", None, c_size_t(0))


def element_types(dim, tag):
    out, n = POINTER(c_int)(), c_size_t()
    _call("gmshModelMeshGetElementTypes", byref(out), byref(n), c_int(dim), c_int(tag))
    return _take_ints(out, n)


def element_count(dim=3):
    """How many elements of dimension *dim* the mesh has."""
    types, tn = POINTER(c_int)(), c_size_t()
    tags, tags_n, tags_nn = POINTER(POINTER(c_size_t))(), POINTER(c_size_t)(), c_size_t()
    nodes, nodes_n, nodes_nn = POINTER(POINTER(c_size_t))(), POINTER(c_size_t)(), c_size_t()
    _call("gmshModelMeshGetElements", byref(types), byref(tn), byref(tags), byref(tags_n),
          byref(tags_nn), byref(nodes), byref(nodes_n), byref(nodes_nn), c_int(dim), c_int(-1))
    total = sum(tags_n[i] for i in range(tags_nn.value))
    lib = load()
    for i in range(tags_nn.value):
        lib.gmshFree(tags[i])
        lib.gmshFree(nodes[i])
    for p, n in ((types, tn.value), (tags, tags_nn.value), (tags_n, tags_nn.value),
                 (nodes, nodes_nn.value), (nodes_n, nodes_nn.value)):
        if n:
            lib.gmshFree(p)
    return total


def field_add(kind):
    load().gmshModelMeshFieldAdd.restype = c_int
    return _call("gmshModelMeshFieldAdd", kind.encode(), c_int(-1))


def field_set_number(tag, option, value):
    _call("gmshModelMeshFieldSetNumber", c_int(tag), option.encode(), c_double(value))


def field_set_numbers(tag, option, values):
    arr = (c_double * len(values))(*values)
    _call("gmshModelMeshFieldSetNumbers", c_int(tag), option.encode(), arr, c_size_t(len(values)))


def field_background(tag):
    _call("gmshModelMeshFieldSetAsBackgroundMesh", c_int(tag))
