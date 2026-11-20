"""Generate solver scripts from a FreeCAD model and an OTTER settings .ini, without a GUI.

The host side copies the inputs into a work folder (so the originals are never touched)
and runs headless/run_otter.py inside FreeCAD's Python, which uses the OTTER add-on's
own generators.
"""
import argparse
import re
import shutil
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from otter_llm.config import otter_addon_dir
from otter_llm.freecad import run_in_freecad
from otter_llm.settings import OtterSettings

SOLVERS = ("openEMS", "EMerge", "Palace")


@dataclass
class GenerateResult:
    ok: bool
    solver: str | None
    out_dir: Path
    files: list[str] = field(default_factory=list)
    messages: list[dict] = field(default_factory=list)
    error: str | None = None
    log: str = ""

    @property
    def warnings(self) -> list[str]:
        """Messages the GUI would show in a message box, as plain text, one per warning.

        The "Simulation script written to: <path>" success box carries the export warnings
        after its first line; the path (a server path in the web app) is left out.
        """
        warnings = []
        for message in self.messages:
            if message["kind"] != "message":
                continue
            text = plain_text(message["text"])
            if text.startswith("Simulation script written to:"):
                lines = [line.strip() for line in text.splitlines()[1:]]
                warnings += [line[2:] for line in lines if line.startswith("- ")]
            else:
                warnings.append(text)
        return warnings


def plain_text(html: str) -> str:
    text = re.sub(r"<br\s*/?>", "\n", html)
    text = re.sub(r"<[^>]+>", "", text)
    return text.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&").strip()


def generate(fcstd, ini, out_dir, solver=None, python_output=True, add_faces=(), save_fcstd=None,
             timeout=600) -> GenerateResult:
    """Write the simulation files for `solver` (default: the one in the settings) into out_dir.

    ini: path of an OTTER settings file, or an OtterSettings.
    add_faces: [{"label": ..., "points": [[x, y, z], ...]}], faces created before loading the
        settings, for ports added in the web app (see otter_llm/ports.py).
    save_fcstd: where to save the model including those faces.
    """
    fcstd, out_dir = Path(fcstd).resolve(), Path(out_dir).resolve()
    if solver is not None and solver.lower() not in [s.lower() for s in SOLVERS]:
        raise ValueError(f"solver must be one of {SOLVERS}")
    out_dir.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="otter_llm_") as work:
        work = Path(work)
        workFcstd = work / fcstd.name
        shutil.copy2(fcstd, workFcstd)
        if isinstance(ini, OtterSettings):
            workIni = work / f"{fcstd.stem}.ini"
            ini.save(workIni)
        else:
            workIni = work / Path(ini).name
            shutil.copy2(Path(ini).resolve(), workIni)

        job = {
            "fcstd": str(workFcstd),
            "ini": str(workIni),
            "out_dir": str(out_dir),
            "solver": solver,
            "python_output": python_output,
            "addon_dir": str(otter_addon_dir()),
            "add_faces": list(add_faces),
            "save_fcstd": str(Path(save_fcstd).resolve()) if save_fcstd else None,
        }
        writable = [out_dir] + ([Path(save_fcstd).resolve().parent] if save_fcstd else [])
        run = run_in_freecad("run_otter.py", job, work, timeout=timeout, writable=writable)

    result = run.result
    if result is None:
        return GenerateResult(ok=False, solver=None, out_dir=out_dir, log=run.log, error=run.error)
    return GenerateResult(ok=result["ok"], solver=result.get("solver"), out_dir=out_dir,
                          files=result.get("files", []), messages=result.get("messages", []),
                          error=result["error"], log=run.log)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Generate OTTER solver scripts without the FreeCAD GUI.")
    parser.add_argument("fcstd", help="FreeCAD model (.FCStd)")
    parser.add_argument("ini", help="OTTER settings file (.ini)")
    parser.add_argument("-o", "--out", required=True, help="output folder for the simulation files")
    parser.add_argument("-s", "--solver", choices=SOLVERS, help="override the solver saved in the .ini")
    parser.add_argument("--octave", action="store_true", help="openEMS only: write Octave instead of Python")
    parser.add_argument("--log", help="save FreeCAD's console output to this file")
    args = parser.parse_args(argv)

    result = generate(args.fcstd, args.ini, args.out, solver=args.solver, python_output=not args.octave)
    if args.log:
        Path(args.log).write_text(result.log)

    for warning in result.warnings:
        print(f"WARNING: {warning}\n")
    if not result.ok:
        print(result.error or "generation failed", file=sys.stderr)
        if not args.log:
            print(result.log[-3000:], file=sys.stderr)
        return 1

    print(f"{result.solver}: {len(result.files)} files in {result.out_dir}")
    for name in result.files:
        print(f"  {name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
