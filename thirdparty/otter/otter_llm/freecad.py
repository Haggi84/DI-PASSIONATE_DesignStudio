"""Run one of the scripts in otter_llm/headless/ inside FreeCAD and collect its result.

FreeCAD runs as the GUI build on Qt's offscreen platform (no display needed). The script
gets its job as JSON and writes a JSON result; see headless/freecad_job.py for that side.

With OTTER_LLM_SANDBOX=1 (the server) every run is wrapped in bubblewrap: an uploaded .FCStd
can run Python when FreeCAD opens it, so FreeCAD gets no network, a read-only view of the
system, FreeCAD and the add-on, and write access only to this job's folders. Other users'
uploads are not visible to it.
"""
import json
import os
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path

from otter_llm.config import PACKAGE_DIR, freecad_launch, otter_addon_dir

HEADLESS_DIR = PACKAGE_DIR / "headless"

# each FreeCAD takes a few hundred MB and a core, more at once only queue up
_slots = threading.BoundedSemaphore(int(os.environ.get("OTTER_LLM_MAX_FREECAD", "4")))


@dataclass
class FreecadRun:
    result: dict | None  # None when FreeCAD stopped before writing a result
    log: str
    returncode: int

    @property
    def error(self) -> str | None:
        if self.result is None:
            return f"FreeCAD exited with code {self.returncode} before writing a result"
        return self.result.get("error")


def sandbox_enabled() -> bool:
    return os.environ.get("OTTER_LLM_SANDBOX", "") not in ("", "0")


def run_in_freecad(script: str, job: dict, work_dir: Path, timeout=600, writable=()) -> FreecadRun:
    """Run headless/<script> (or a script by absolute path) with `job`.

    work_dir holds job, result and log files and the job's inputs; `writable` are the other
    folders the script writes to (generated files).
    """
    job = dict(job, result_file=str(work_dir / "result.json"), log_file=str(work_dir / "freecad.log"))
    jobFile = work_dir / "job.json"
    jobFile.write_text(json.dumps(job, indent=2))

    scriptPath = HEADLESS_DIR / script  # an absolute script path replaces HEADLESS_DIR
    command, launchEnv = freecad_launch()
    command = command + [str(scriptPath)]
    jobEnv = {**launchEnv, "OTTER_LLM_JOB": str(jobFile), "OTTER_LLM_HEADLESS_DIR": str(HEADLESS_DIR),
              "QT_QPA_PLATFORM": "offscreen"}
    if sandbox_enabled():
        command, env = sandboxed(command, work_dir, [Path(p) for p in writable], scriptPath, jobEnv)
    else:
        env = dict(os.environ, **jobEnv)

    with _slots:
        proc = subprocess.run(command, cwd=work_dir, env=env, capture_output=True, text=True, timeout=timeout)

    logFile, resultFile = Path(job["log_file"]), Path(job["result_file"])
    log = (logFile.read_text(errors="replace") if logFile.exists() else "") + proc.stdout + proc.stderr
    result = json.loads(resultFile.read_text()) if resultFile.exists() else None
    return FreecadRun(result=result, log=log, returncode=proc.returncode)


def sandboxed(command, work_dir: Path, writable: list[Path], scriptPath: Path, jobEnv: dict):
    """bubblewrap command line and environment for one FreeCAD run."""
    home = work_dir / "home"
    home.mkdir(exist_ok=True)
    readOnly = {otter_addon_dir().resolve(), PACKAGE_DIR, scriptPath.resolve().parent}
    freecadDir = Path(command[0]).resolve().parent
    if not str(freecadDir).startswith("/usr/"):  # a system FreeCAD is already in the /usr bind
        if freecadDir.name == "bin" and freecadDir.parent.name == "usr":
            freecadDir = freecadDir.parent.parent  # extracted AppImage: its whole folder
        readOnly.add(freecadDir)

    args = ["bwrap", "--die-with-parent", "--new-session", "--unshare-all",
            "--ro-bind", "/usr", "/usr", "--ro-bind", "/etc", "/etc",
            "--symlink", "usr/lib", "/lib", "--symlink", "usr/lib64", "/lib64",
            "--symlink", "usr/bin", "/bin", "--symlink", "usr/sbin", "/sbin",
            "--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp", "--tmpfs", "/run"]
    for path in sorted(readOnly):
        args += ["--ro-bind", str(path), str(path)]
    for path in [work_dir] + writable:
        path.mkdir(parents=True, exist_ok=True)
        args += ["--bind", str(path), str(path)]
    args += ["--chdir", str(work_dir), "--"]

    env = {"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "HOME": str(home), "TMPDIR": "/tmp", **jobEnv}
    return args + command, env
