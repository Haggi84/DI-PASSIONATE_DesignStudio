"""Where FreeCAD and the OTTER add-on live. Both can be set with environment variables."""
import glob
import os
import shutil
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
PROJECT_DIR = PACKAGE_DIR.parent  # OTTER_Local


def otter_addon_dir() -> Path:
    """OTTER add-on folder; default is the OTTER copy inside OTTER_Local."""
    path = Path(os.environ.get("OTTER_ADDON_DIR", PROJECT_DIR / "OTTER" / "addon")).expanduser()
    if not (path / "OtterDialog.py").is_file():
        raise FileNotFoundError(f"OTTER add-on not found at {path}, set OTTER_ADDON_DIR")
    return path


def freecad_command() -> list[str]:
    """Command that starts FreeCAD with its GUI; the caller sets QT_QPA_PLATFORM=offscreen.

    OTTER_LLM_FREECAD may point to a FreeCAD binary or a FreeCAD AppImage. freecadcmd
    is not enough, it does not load object colours.
    """
    candidates = []
    if os.environ.get("OTTER_LLM_FREECAD"):
        candidates.append(os.path.expanduser(os.environ["OTTER_LLM_FREECAD"]))
    for name in ("freecad", "FreeCAD"):
        if shutil.which(name):
            candidates.append(shutil.which(name))
    candidates += sorted(glob.glob(os.path.expanduser("~/Downloads/FreeCAD_*.AppImage")), reverse=True)
    candidates += sorted(glob.glob(os.path.expanduser("~/Applications/FreeCAD_*.AppImage")), reverse=True)

    for path in candidates:
        if os.path.isfile(path) and os.access(path, os.X_OK):
            return [path]
    raise FileNotFoundError("FreeCAD not found, set OTTER_LLM_FREECAD to the FreeCAD binary or AppImage")


def freecad_launch() -> tuple[list[str], dict]:
    """Command and extra environment that start FreeCAD on Qt's offscreen platform.

    An AppImage's AppRun forces QT_QPA_PLATFORM=xcb, which needs an X display (on a desktop
    FreeCAD then uses it without showing a window; on a server it aborts). For an extracted
    AppImage (OTTER_LLM_FREECAD=.../squashfs-root/AppRun) usr/bin/freecad is started directly
    with AppRun's other settings, so offscreen holds. A packed .AppImage needs a display.
    """
    command = freecad_command()
    path = Path(command[0])
    binary = path.parent / "usr" / "bin" / "freecad"
    if path.name == "AppRun" and binary.is_file():
        usr = path.parent.resolve() / "usr"
        return [str(usr / "bin" / "freecad")], {
            "PREFIX": str(usr), "PYTHONHOME": str(usr), "PATH_TO_FREECAD_LIBDIR": str(usr / "lib"),
            "FONTCONFIG_FILE": "/etc/fonts/fonts.conf", "FONTCONFIG_PATH": "/etc/fonts",
            "SSL_CERT_FILE": str(usr / "ssl" / "cacert.pem"),
        }
    return command, {}
