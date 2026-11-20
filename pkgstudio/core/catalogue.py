# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
Housing catalogues: MirrorSemi's open-cavity packages, and local folders.

MirrorSemi (mirrorsemi.com) sells pre-moulded open-cavity QFN and DFN
packages, ready for die attach and wire bonding, and publishes a STEP model
and a bonding diagram for each. Their files are "all rights reserved", so
none is shipped with the workbench: the catalogue page is read and a model
downloaded only when you ask, into a cache in your FreeCAD user folder, for
your own use. Some catalogue links lead to a 404 page; a download is
accepted only if it really is a STEP file.

A local folder is any folder of STEP files — your own housings, or a
supplier's models you have downloaded.
"""

import os
import re
import urllib.parse
import urllib.request
from dataclasses import dataclass

MIRRORSEMI = "https://www.mirrorsemi.com/CAD.html"
STEP_EXT = (".step", ".stp")


@dataclass
class Entry:
    name: str
    source: str            # "MirrorSemi" or a folder path
    step: str              # URL or file path
    bond: str = ""         # bonding diagram URL (DXF), if any
    leads: int = 0
    pitch: float = 0.0
    family: str = ""

    def describe(self):
        bits = [self.name]
        if self.leads:
            bits.append(f"{self.leads} leads")
        if self.pitch:
            bits.append(f"{self.pitch:g} mm pitch")
        return ", ".join(bits)


_NAME = re.compile(r"(?P<fam>[A-Z]-?(?:QFN|DFN|LGA|SON))(?P<n>\d+)[WT]?(?P<pitch>\.\d+)", re.I)


def parse_name(name):
    """(family, leads, pitch mm) from a part name like M-QFN16W.65; zeros if unknown."""
    m = _NAME.search(name)
    if not m:
        return "", 0, 0.0
    return m.group("fam").upper(), int(m.group("n")), float("0" + m.group("pitch"))


def parse_mirrorsemi(html, base=MIRRORSEMI):
    """[Entry] for every STEP model on MirrorSemi's CAD page, with its
    bonding diagram where one is listed."""
    steps = re.findall(r'href="([^"]+\.(?:step|stp))"', html, re.I)
    bonds = re.findall(r'href="([^"]+_Bond\.dxf)"', html, re.I)
    out, seen = [], set()
    for href in steps:
        stem = os.path.splitext(os.path.basename(href))[0]
        if stem.lower() in seen:
            continue
        seen.add(stem.lower())
        key = stem.split("-G")[0].lower()
        bond = next((b for b in bonds if os.path.basename(b).lower().startswith(key)), "")
        fam, n, pitch = parse_name(stem)
        out.append(Entry(name=stem, source="MirrorSemi", step=urllib.parse.urljoin(base, href),
                         bond=urllib.parse.urljoin(base, bond) if bond else "",
                         leads=n, pitch=pitch, family=fam))
    return sorted(out, key=lambda e: (e.family, e.leads, e.pitch, e.name))


def fetch_mirrorsemi(timeout=20):
    req = urllib.request.Request(MIRRORSEMI, headers={"User-Agent": "FreeCAD PackageStudio"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return parse_mirrorsemi(r.read().decode("utf-8", errors="ignore"))


def cache_dir():
    try:
        import FreeCAD
        root = FreeCAD.getUserAppDataDir()
    except Exception:
        root = os.path.expanduser("~")
    path = os.path.join(root, "PackageStudio", "cache", "mirrorsemi")
    os.makedirs(path, exist_ok=True)
    return path


def is_step(path):
    try:
        with open(path, "rb") as fh:
            return fh.read(64).lstrip().startswith(b"ISO-10303-21")
    except OSError:
        return False


def download(entry, timeout=60):
    """The local path of *entry*'s STEP model, downloading it into the cache
    first if needed. Raises IOError when the link does not lead to a STEP file."""
    if entry.source != "MirrorSemi":
        return entry.step
    path = os.path.join(cache_dir(), os.path.basename(urllib.parse.urlparse(entry.step).path))
    if is_step(path):
        return path
    req = urllib.request.Request(entry.step, headers={"User-Agent": "FreeCAD PackageStudio"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = r.read()
    if not data.lstrip().startswith(b"ISO-10303-21"):
        raise IOError(f"{entry.name}: MirrorSemi's link does not lead to a STEP model "
                      f"({len(data)} bytes, not STEP) — the file may have moved.")
    with open(path, "wb") as fh:
        fh.write(data)
    return path


def scan_folder(folder):
    """[Entry] for every STEP file in *folder* (not recursive)."""
    out = []
    if folder and os.path.isdir(folder):
        for f in sorted(os.listdir(folder)):
            if f.lower().endswith(STEP_EXT):
                fam, n, pitch = parse_name(f)
                out.append(Entry(name=os.path.splitext(f)[0], source=folder,
                                 step=os.path.join(folder, f), leads=n, pitch=pitch, family=fam))
    return out
