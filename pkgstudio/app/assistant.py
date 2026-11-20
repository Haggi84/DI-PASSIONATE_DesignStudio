# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
The EM assistant: a language model that changes the EM setup through a fixed
set of tools and explains OTTER's findings.

The conversation, the tools and the documentation search are OTTER_Local's
(otter_llm.assistant): the model can only call the tools in tools.py — solver,
frequency, material per layer, mesh, ports, search, checks — and every call
goes through the same validation as a click in the panel. It never sees or
writes solver code; the OTTER add-on writes that from the settings. So a
wrong or manipulated answer can at worst pick a bad setting, which the checks
and the person see.

What is replaced is the transport. OTTER_Local talks to the providers with
the anthropic SDK and httpx, neither of which FreeCAD's Python has, and this
workbench depends on nothing but gdstk. The three APIs are plain JSON over
HTTPS, so urllib is enough.

Tool calls do not run here directly but through *tool_runner*: the panel runs
the conversation in a worker thread and must make every change to the
project (and so to the simulation document) on the GUI thread.

Qt-free, so it is testable headlessly with a fake transport.
"""

import json
import socket
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field

from ..core import otter_path

otter_path.ensure()

from otter_llm.assistant import agent as _otter_agent  # noqa: E402
from otter_llm.assistant.knowledge import format_sections, knowledge_for  # noqa: E402
from otter_llm.assistant.tools import TOOLS, ToolBox, describe_project, tool_args  # noqa: E402
from otter_llm.starter import MATERIALS  # noqa: E402

MAX_STEPS = 10
TIMEOUT_S = 180

CLAUDE_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"
CLAUDE_MODELS = dict(_otter_agent.CLAUDE_MODELS)
GEMINI_URL = _otter_agent.GEMINI_URL
GEMINI_MODELS = dict(_otter_agent.GEMINI_MODELS)
OLLAMA_URL = "http://127.0.0.1:11434"
OLLAMA_DEFAULT_MODEL = "qwen3:8b"
# Context window asked for in every request. Ollama's default window is
# smaller than system prompt + tools + project state + findings + history, and
# Ollama then silently drops the *start* of the conversation — the
# instructions — so the model stops calling tools and answers beside the
# point. OTTER_Local starts its own Ollama with this window; here the person's
# own Ollama runs, so the request asks for it.
OLLAMA_CONTEXT = 16384
# Seconds without a single streamed token before the request is given up.
# The answer streams, so a slow CPU model only has to keep producing: there is
# no limit on the whole answer. The first token may wait for the model to load.
OLLAMA_IDLE_S = 300

PROVIDERS = ("ollama", "claude", "gemini")

_INTRO = """You are the EM simulation assistant of Package Studio, a FreeCAD workbench for chip packaging \
assemblies: chips imported from GDS against a PDK stackup, housings with leadframes, lids, boards, bond wires \
and ports. It turns the open assembly into a flat simulation model — the chips' metal rebuilt exactly from the \
layout inside an EM region round the wired pads — and writes a ready-to-run package for openEMS (FDTD), EMerge \
(FEM) or Palace (FEM) with the OTTER add-on; you help the user get the settings right.

Materials: most layers have a material on record, from the PDK stackup (for example "SG13G2 / TopMetal2", with \
the PDK's conductivity) or from the Materials panel (leadframe copper alloy, mould compound, FR-4, the wire \
metal). The <material_origins> list says where each came from. Layers marked "guessed by OTTER" have no record; \
review those first and say which look wrong. Do not change a material that comes from the stackup unless the \
user asks. A via array is one block with its conductivity scaled by its fill; that is intended.

<readiness> lists what the workbench found missing in the assembly itself (a port, a material, an unplaced \
chip). You cannot fix those with your tools; tell the user which command does, as the list says.

"""
SYSTEM = _INTRO + _otter_agent.SYSTEM[_otter_agent.SYSTEM.index("What you can do:"):]


def current_tools():
    """OTTER's tool definitions with the material list as it is now: stackup
    materials are registered when a model is built, after tools.py was imported."""
    tools = json.loads(json.dumps(TOOLS))
    for tool in tools:
        if tool["name"] == "set_material":
            tool["input_schema"]["properties"]["material"]["enum"] = list(MATERIALS) + ["not simulated"]
    return tools


def _claude_tools():
    # "strict" asks for schema-exact arguments where the model supports it;
    # left out so the request is accepted by every model and API version.
    # The ToolBox validates every argument anyway.
    return [{k: v for k, v in tool.items() if k != "strict"} for tool in current_tools()]


def _ollama_tools():
    return [{"type": "function", "function": {"name": t["name"], "description": t["description"],
                                              "parameters": t["input_schema"]}} for t in current_tools()]


def _gemini_tools():
    return [{"functionDeclarations": [{"name": t["name"], "description": t["description"],
                                       "parametersJsonSchema": t["input_schema"]} for t in current_tools()]}]


@dataclass
class Event:
    kind: str           # "text" | "tool" | "error"
    text: str
    tool: str = ""
    changed: bool = False


class TransportError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


_LOCAL_HOSTS = ("127.0.0.1", "localhost", "::1", "0.0.0.0")
# No proxy for this computer. On Windows urllib takes the system proxy from the
# registry, and its bypass list ("<local>") only covers names without a dot —
# so a request to 127.0.0.1 went to the institute's proxy and failed there.
_DIRECT = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _opener(url):
    host = (urllib.parse.urlsplit(url).hostname or "").lower()
    return _DIRECT if host in _LOCAL_HOSTS else urllib.request.build_opener()


def _http_error(error):
    body = error.read().decode("utf-8", errors="replace")
    try:
        detail = json.loads(body).get("error", {})
        message = detail.get("message") if isinstance(detail, dict) else str(detail)
    except ValueError:
        message = body[:300]
    return TransportError(error.code, message or error.reason)


def http_post(url, payload, headers, timeout=TIMEOUT_S):
    """POST JSON, return the decoded JSON reply; raise TransportError otherwise."""
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=data, method="POST",
                                     headers={"content-type": "application/json", **headers})
    try:
        with _opener(url).open(request, timeout=timeout) as reply:
            return json.loads(reply.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        raise _http_error(error) from None
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise TransportError(None, str(getattr(error, "reason", error))) from None


def http_get(url, timeout=3):
    with _opener(url).open(url, timeout=timeout) as reply:
        return json.loads(reply.read().decode("utf-8"))


def ollama_chat(url, payload, headers=None, idle_s=OLLAMA_IDLE_S, progress=None):
    """
    POST /api/chat with streaming and put the streamed message together:
    {"role", "content", "thinking", "tool_calls"}.

    *progress(kind, n)* is called as tokens arrive ("thinking" or "answer",
    count so far), from the calling thread.
    """
    body = dict(payload, stream=True)
    data = json.dumps(body).encode("utf-8")
    request = urllib.request.Request(url, data=data, method="POST",
                                     headers={"content-type": "application/json", **(headers or {})})
    message = {"role": "assistant", "content": "", "thinking": "", "tool_calls": []}
    counts = {"thinking": 0, "answer": 0}
    try:
        with _opener(url).open(request, timeout=idle_s) as reply:
            for raw in reply:
                line = raw.strip()
                if not line:
                    continue
                chunk = json.loads(line.decode("utf-8"))
                if chunk.get("error"):
                    raise TransportError(500, str(chunk["error"]))
                part = chunk.get("message") or {}
                if part.get("thinking"):
                    message["thinking"] += part["thinking"]
                    counts["thinking"] += 1
                    if progress:
                        progress("thinking", counts["thinking"])
                if part.get("content"):
                    message["content"] += part["content"]
                    counts["answer"] += 1
                    if progress:
                        progress("answer", counts["answer"])
                if part.get("tool_calls"):
                    message["tool_calls"].extend(part["tool_calls"])
                if chunk.get("done"):
                    break
    except urllib.error.HTTPError as error:
        raise _http_error(error) from None
    except TransportError:
        raise
    except (TimeoutError, socket.timeout):
        raise TransportError(None, f"no answer for {idle_s} s") from None
    except (urllib.error.URLError, OSError) as error:
        reason = getattr(error, "reason", error)
        if isinstance(reason, (TimeoutError, socket.timeout)):
            raise TransportError(None, f"no answer for {idle_s} s") from None
        raise TransportError(None, str(reason)) from None
    except ValueError as error:
        raise TransportError(None, f"unreadable reply: {error}") from None
    if not message["tool_calls"]:
        del message["tool_calls"]
    return message


def ollama_models(url=OLLAMA_URL):
    """Names of the models the Ollama at *url* has, or None when it does not answer."""
    try:
        tags = http_get(f"{url.rstrip('/')}/api/tags")
    except Exception:
        return None
    return [m.get("name") or m.get("model") for m in tags.get("models", [])]


def _same_model(wanted, available):
    """'qwen3:8b' is 'qwen3:8b'; 'qwen3' is 'qwen3:latest'."""
    wanted = wanted if ":" in wanted else f"{wanted}:latest"
    return any((name if ":" in name else f"{name}:latest") == wanted for name in available)


def check_ollama(url=OLLAMA_URL, model=OLLAMA_DEFAULT_MODEL, idle_s=OLLAMA_IDLE_S):
    """
    (ok, text): whether the assistant can work with *model* at *url*, and
    what to do if not. Asks the model one short question, so it also loads it.
    """
    import time
    base = url.rstrip("/")
    try:
        version = http_get(f"{base}/api/version").get("version", "?")
    except Exception as error:
        reason = getattr(error, "reason", error)
        return False, (f"Ollama does not answer at {base} ({reason}). Start Ollama (tray icon, or "
                       "'ollama serve'), or check the URL.")
    available = ollama_models(base) or []
    if not _same_model(model, available):
        listed = ", ".join(available) if available else "none"
        return False, (f"Ollama {version} runs, but the model '{model}' is not there (available: {listed}). "
                       f"In a terminal: ollama pull {model}")
    started = time.time()
    try:
        reply = ollama_chat(f"{base}/api/chat", {
            "model": model, "messages": [{"role": "user", "content": "Answer with the single word: OK"}],
            "options": {"temperature": 0, "num_ctx": OLLAMA_CONTEXT}, "keep_alive": "30m"}, idle_s=idle_s)
    except TransportError as error:
        return False, f"Ollama {version}, model {model}: the test question failed: {error}"
    seconds = time.time() - started
    answer = (reply.get("content") or "").strip()[:40] or "(empty)"
    slow = " That is slow; a smaller model (qwen3:4b) answers faster." if seconds > 60 else ""
    return True, f"Ollama {version}, {model}: answered '{answer}' in {seconds:.0f} s.{slow}"


def material_origins(project):
    rows = project.material_table() if hasattr(project, "material_table") else []
    return "\n".join(f"- {group}: {material or 'not simulated'} ({origin})"
                     for group, _z0, _z1, material, origin, _labels in rows)


def readiness_text(project):
    """The readiness findings of the project's assembly, one per line, or ''."""
    try:
        import FreeCAD
        from . import readiness
        doc = FreeCAD.getDocument(project.sim.source_doc)
        found = readiness.check(doc, project.sim.regions) + readiness.check_model(project.sim)
    except Exception:
        return ""
    return "\n".join(f"- {f}" for f in found if f.level != "info")


@dataclass
class Assistant:
    provider: str
    toolbox: ToolBox
    model: str = ""
    api_key: str = field(default="", repr=False)
    ollama_url: str = OLLAMA_URL
    tool_runner: object = None      # (name, args) -> (text, changed); default toolbox.run
    transport: object = None        # (url, payload, headers) -> dict; default http_post / ollama_chat
    progress: object = None         # (kind, count) while a local model streams; any thread
    history: list = field(default_factory=list)

    def __post_init__(self):
        if self.provider not in PROVIDERS:
            raise ValueError(f"provider must be one of {PROVIDERS}")
        self.tool_runner = self.tool_runner or self.toolbox.run
        self._ollama_transport = self.transport or (
            lambda url, payload, headers: ollama_chat(url, payload, headers, progress=self.progress))
        self.transport = self.transport or http_post

    def reset(self):
        self.history = []

    def user_turn(self, question):
        project = self.toolbox.project
        found = self.toolbox.knowledge.search(question, k=3) if self.toolbox.knowledge else []
        parts = [f"<project_state>\n{describe_project(project)}\n</project_state>"]
        origins = material_origins(project)
        if origins:
            parts.append(f"<material_origins>\n{origins}\n</material_origins>")
        readiness = readiness_text(project)
        if readiness:
            parts.append(f"<readiness>\n{readiness}\n</readiness>")
        if found:
            parts.append(f"<findings>\n{format_sections(found)}\n</findings>")
        parts.append(f"<question>\n{question}\n</question>")
        return "\n\n".join(parts)

    def ask(self, question):
        """Answer one question; returns the events (text, tool calls, errors) in order."""
        if self.provider == "claude":
            if not self.api_key:
                return [Event("error", "No Anthropic API key set (EM Assistant panel).")]
            return self._ask_claude(question)
        if self.provider == "gemini":
            if not self.api_key:
                return [Event("error", "No Google API key set (EM Assistant panel).")]
            return self._ask_gemini(question)
        return self._ask_ollama(question)

    def _run_tool(self, name, args, events):
        result, changed = self.tool_runner(name, tool_args(args))
        events.append(Event("tool", result, tool=name, changed=changed))
        return result

    # -- Claude --------------------------------------------------------------------------------

    def _ask_claude(self, question):
        start = len(self.history)
        self.history.append({"role": "user", "content": self.user_turn(question)})
        events = []
        headers = {"x-api-key": self.api_key, "anthropic-version": ANTHROPIC_VERSION}
        for _ in range(MAX_STEPS):
            try:
                reply = self.transport(CLAUDE_URL, {
                    "model": self.model or "claude-sonnet-5-5", "max_tokens": 8000, "system": SYSTEM,
                    "tools": _claude_tools(), "messages": self.history}, headers)
            except TransportError as error:
                del self.history[start:]
                return events + [Event("error", claude_error(error))]
            content = reply.get("content") or []
            self.history.append({"role": "assistant", "content": content})
            stop = reply.get("stop_reason")
            if stop == "refusal":
                return events + [Event("error", "Claude declined to answer this request.")]
            if stop == "max_tokens":
                return events + [Event("error", "The answer was too long and got cut off.")]
            text = "".join(b.get("text", "") for b in content if b.get("type") == "text").strip()
            calls = [b for b in content if b.get("type") == "tool_use"]
            if text and calls:
                events.append(Event("text", text))
            if not calls:
                return events + [Event("text", text or "(no answer)")]
            results = []
            for call in calls:
                result = self._run_tool(call.get("name", ""), call.get("input"), events)
                results.append({"type": "tool_result", "tool_use_id": call.get("id"), "content": result,
                                "is_error": result.startswith("error")})
            self.history.append({"role": "user", "content": results})
        return events + [Event("error", f"Stopped after {MAX_STEPS} steps.")]

    # -- Gemini --------------------------------------------------------------------------------

    def _ask_gemini(self, question):
        model = self.model or "gemini-3.8-flash"
        start = len(self.history)
        self.history.append({"role": "user", "parts": [{"text": self.user_turn(question)}]})
        events = []

        def failed(text):       # Gemini needs the history to alternate user/model
            del self.history[start:]
            return events + [Event("error", text)]

        for _ in range(MAX_STEPS):
            try:
                data = self.transport(f"{GEMINI_URL}/{model}:generateContent", {
                    "systemInstruction": {"parts": [{"text": SYSTEM}]}, "contents": self.history,
                    "tools": _gemini_tools(), "generationConfig": {"maxOutputTokens": 8000}},
                    {"x-goog-api-key": self.api_key})
            except TransportError as error:
                return failed(gemini_error(error))
            candidates = data.get("candidates") or []
            if not candidates:
                reason = (data.get("promptFeedback") or {}).get("blockReason", "no answer")
                return failed(f"Gemini declined to answer this request ({reason}).")
            candidate = candidates[0]
            parts = (candidate.get("content") or {}).get("parts") or []
            finish = candidate.get("finishReason", "STOP")
            if finish == "MAX_TOKENS":
                return failed("The answer was too long and got cut off.")
            if not parts:
                return failed(f"Gemini stopped without an answer ({finish}).")
            self.history.append({"role": "model", "parts": parts})
            text = "".join(p.get("text", "") for p in parts if not p.get("thought")).strip()
            calls = [p["functionCall"] for p in parts if "functionCall" in p]
            if text and calls:
                events.append(Event("text", text))
            if not calls:
                return events + [Event("text", text or "(no answer)")]
            results = []
            for call in calls:
                name = call.get("name", "")
                result = self._run_tool(name, call.get("args"), events)
                response = {"name": name, "response": {"error" if result.startswith("error") else "result": result}}
                if call.get("id"):
                    response["id"] = call["id"]
                results.append({"functionResponse": response})
            self.history.append({"role": "user", "parts": results})
        return failed(f"Stopped after {MAX_STEPS} steps.")

    # -- Ollama --------------------------------------------------------------------------------

    def _ask_ollama(self, question):
        if not self.history:
            self.history.append({"role": "system", "content": SYSTEM})
        start = len(self.history)
        self.history.append({"role": "user", "content": self.user_turn(question)})
        events = []
        for _ in range(MAX_STEPS):
            model = self.model or OLLAMA_DEFAULT_MODEL
            try:
                reply = self._ollama_transport(f"{self.ollama_url.rstrip('/')}/api/chat", {
                    "model": model, "messages": self.history, "tools": _ollama_tools(),
                    "options": {"temperature": 0.2, "num_ctx": OLLAMA_CONTEXT}, "keep_alive": "30m"}, {})
            except TransportError as error:
                del self.history[start:]
                return events + [Event("error", ollama_error(error, self.ollama_url, model))]
            # a streamed reply is the message itself, a test transport may wrap it
            message = reply.get("message", reply) if isinstance(reply, dict) else {}
            self.history.append({k: message[k] for k in ("role", "content", "tool_calls") if k in message})
            text = (message.get("content") or "").strip()
            calls = message.get("tool_calls") or []
            if text and calls:
                events.append(Event("text", text))
            if not calls:
                return events + [Event("text", text or "(no answer)")]
            for call in calls:
                function = call.get("function", {})
                name = function.get("name", "")
                result = self._run_tool(name, function.get("arguments"), events)
                self.history.append({"role": "tool", "content": result, "tool_name": name})
        return events + [Event("error", f"Stopped after {MAX_STEPS} steps.")]


def ollama_error(error, url, model):
    text = str(error)
    if error.status == 404 or "not found" in text.lower():
        return f"Ollama does not have the model '{model}'. In a terminal: ollama pull {model}"
    if error.status is None and "no answer" in text:
        return (f"The model '{model}' gave no answer for {OLLAMA_IDLE_S} s. On a CPU a smaller model "
                "(qwen3:4b) is much faster; the Test button shows how long one answer takes.")
    if error.status is None:
        return (f"Ollama does not answer at {url} ({text}). Is it running (tray icon, or 'ollama serve')? "
                "The Test button checks the connection and the model.")
    return f"Ollama error {error.status}: {text}"


def claude_error(error):
    if error.status in (401,):
        return "The Anthropic API key was not accepted. Check it on platform.claude.com."
    if error.status == 403:
        return f"This API key may not use this model: {error}"
    if error.status == 429:
        return "Rate limit of this API key reached, try again in a minute."
    if error.status is None:
        return f"Could not reach the Claude API: {error}"
    return f"Claude API error {error.status}: {error}"


def gemini_error(error):
    text = str(error)
    if error.status in (401, 403) or "API key" in text:
        return "The Google API key was not accepted. Check it on aistudio.google.com."
    if error.status == 429:
        return "Rate limit or free quota of this API key reached, try again in a minute."
    if error.status is None:
        return f"Could not reach the Gemini API: {text}"
    return f"Gemini API error {error.status}: {text}"


def make_toolbox(project):
    """A ToolBox on *project* with OTTER's documentation as its knowledge."""
    return ToolBox(project, knowledge_for(otter_path.OTTER_DIR))
