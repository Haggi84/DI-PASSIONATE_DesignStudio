# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
EM settings that outlive a session: which solver Python the package's run
command uses, where packages go, and which language model the assistant uses.

Kept in FreeCAD's own parameter store (user.cfg), like every other workbench
setting. API keys are stored there only when the person ticks "remember";
user.cfg is a plain text file in the user's FreeCAD folder, which the
checkbox's tooltip says.
"""

import os

import FreeCAD

_PATH = "User parameter:BaseApp/Preferences/Mod/PackageStudio/EM"

_STRINGS = {
    "OutputDir": "",
    "SolverPython": "",
    "PalaceBin": "",
    "Mpirun": "",
    "Provider": "ollama",
    "Model": "",
    "OllamaUrl": "http://127.0.0.1:11434",
    "ClaudeKey": "",
    "GeminiKey": "",
}

# Keys held for this session only, when "remember" is not ticked.
_session_keys = {}


def _group():
    return FreeCAD.ParamGet(_PATH)


def get(name):
    value = _group().GetString(name, _STRINGS.get(name, ""))
    return value if value else _STRINGS.get(name, "")


def put(name, value):
    _group().SetString(name, str(value or ""))


def api_key(provider):
    name = {"claude": "ClaudeKey", "gemini": "GeminiKey"}.get(provider)
    if not name:
        return ""
    env = {"claude": "ANTHROPIC_API_KEY", "gemini": "GEMINI_API_KEY"}[provider]
    return _session_keys.get(provider) or get(name) or os.environ.get(env, "")


def set_api_key(provider, key, remember):
    name = {"claude": "ClaudeKey", "gemini": "GeminiKey"}.get(provider)
    if not name:
        return
    _session_keys[provider] = key
    put(name, key if remember else "")


def key_is_remembered(provider):
    name = {"claude": "ClaudeKey", "gemini": "GeminiKey"}.get(provider)
    return bool(name and get(name))


def apply_run_environment():
    """
    The variables otter_llm's run notes read, so the package README tells
    the person the command for their own solver Python and Palace.
    """
    for env, name in (("OTTER_LLM_SOLVER_PYTHON", "SolverPython"), ("OTTER_LLM_PALACE_BIN", "PalaceBin"),
                      ("OTTER_LLM_MPIRUN", "Mpirun")):
        value = get(name)
        if value:
            os.environ[env] = value
        else:
            os.environ.pop(env, None)
