"""Job handling shared by the scripts FreeCAD runs (see otter_llm/freecad.py for the host side).

A script does:
    sys.path.insert(0, os.environ["OTTER_LLM_HEADLESS_DIR"])
    from freecad_job import runJob
    runJob(work)          # work(job, result) fills the result dict

The job path comes in the OTTER_LLM_JOB environment variable because FreeCAD does not pass
script arguments through cleanly.
"""
import json
import os
import sys
import traceback


def runJob(work):
    with open(os.environ["OTTER_LLM_JOB"]) as f:
        job = json.load(f)

    # the GUI sends print() to its Report view, console output goes to the log instead
    log = open(job["log_file"], "w", buffering=1)
    sys.stdout = sys.stderr = log

    result = {"ok": False, "error": None}
    try:
        import FreeCAD
        if not FreeCAD.GuiUp:
            raise RuntimeError("FreeCAD started without its GUI, run the FreeCAD (not freecadcmd) binary")
        work(job, result)
        result["ok"] = True
    except Exception:
        result["error"] = traceback.format_exc()
    finally:
        with open(job["result_file"], "w") as f:
            json.dump(result, f, indent=2)
        log.close()
        # otherwise FreeCAD keeps running after the script, so leave hard
        os._exit(0)
