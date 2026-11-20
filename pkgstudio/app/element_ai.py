# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
New elements from a sentence or a datasheet, by a local model (Ollama).

The model never writes geometry. It fills in core.elements' schema — Ollama's
structured output (`format` = the JSON schema) constrains it token by token,
so the answer is always a complete spec of the right types — and says which
values it assumed. The spec is then checked by elements.validate; if it has
errors, the model gets them back once or twice to correct ("repair"), with
the exact numbers the check computed. Whatever is still wrong is shown in the
editor with a fix to apply; nothing is built until the person inserts it.

Two modes, the same call:

  new     "QFN-24 4x4, 0.5 mm pitch, exposed pad 2.6" — or a pasted package
          drawing table (D, E, A, e, b, L, D2, E2)
  change  the current spec plus "pitch 0.4, two more leads per side": the
          model returns the whole spec, changed only where asked

The answer streams: *on_field(key, value)* is called as each value is
complete in the stream, so the editor fills its fields one by one while the
model is still writing.

Transport: plain urllib (FreeCAD's Python has nothing else), never through a
proxy for this computer — the same reason as in assistant.py. Qt-free and
FreeCAD-free, testable with a fake *chat*.
"""

import json
import re
import socket
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field

from ..core import elements as el

OLLAMA_URL = "http://127.0.0.1:11434"
DEFAULT_MODEL = "qwen3:8b"
CONTEXT = 8192
IDLE_S = 300
MAX_REPAIRS = 2

SYSTEM = f"""You are an IC packaging engineer. You turn a description of a semiconductor package — a sentence, \
a part number, or the dimension table of a datasheet's package drawing — into the numbers of an open-cavity \
package model. Answer with JSON only, following the schema.

Fields (all lengths in millimetres):
{el.field_guide()}

Rules:
- Use the values the user gives exactly. Datasheet symbols: D = body_x, E = body_y, A = height, e = pitch, \
b = lead_w, L = lead_len, D2 = paddle_x, E2 = paddle_y, A1 = standoff. Where min/nom/max are given, take nom.
- per_side_x is the number of leads on EACH of the left and right sides, per_side_y on EACH of the bottom and \
top sides. A QFN-24 has per_side_x = per_side_y = 6. A DFN-8 has per_side_x = 4, per_side_y = 0. \
A SOIC-8 or TSSOP-8 has per_side_x = 0, per_side_y = 4.
- For every value the user did not give, choose the typical JEDEC value for this family and size, and list the \
field in "assumed". Never list a field the user gave.
- The name gives family, lead count and body size, e.g. "QFN-24 4x4".
- For QFN/DFN the exposed pad (paddle) is typically 55–70 % of the body, and lead_len (L) 0.3–0.5 mm.

Example: "QFN-16, 4 mm body, 0.65 pitch, exposed pad 2.3 mm" gives
{json.dumps(dict(el.EXAMPLE, assumed=["height", "lead_w", "finger", "lead_len", "lead_t", "foot", "standoff",
                                      "standard"],
                 notes="Height, lead width and thicknesses are MO-220 typical values."))}
"""


@dataclass
class Draft:
    spec: dict
    assumed: list = field(default_factory=list)
    notes: str = ""
    issues: list = field(default_factory=list)
    attempts: int = 1
    model: str = ""
    prompt: str = ""
    raw: str = ""

    @property
    def ok(self):
        return not el.errors(self.issues)

    def entry(self):
        return el.Entry(dict(self.spec), list(self.assumed), self.notes,
                        {"by": "ollama", "model": self.model, "prompt": self.prompt})


class GenerationError(Exception):
    pass


# -- transport -----------------------------------------------------------------------------------

_LOCAL = ("127.0.0.1", "localhost", "::1", "0.0.0.0")
_DIRECT = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _opener(url):
    host = (urllib.parse.urlsplit(url).hostname or "").lower()
    return _DIRECT if host in _LOCAL else urllib.request.build_opener()


def stream_chat(url, payload, on_text=None, idle_s=IDLE_S):
    """POST /api/chat streaming; returns the answer's text. *on_text(chunk)*
    for every piece as it arrives."""
    data = json.dumps(dict(payload, stream=True)).encode("utf-8")
    request = urllib.request.Request(f"{url.rstrip('/')}/api/chat", data=data, method="POST",
                                     headers={"content-type": "application/json"})
    text = []
    try:
        with _opener(url).open(request, timeout=idle_s) as reply:
            for raw in reply:
                line = raw.strip()
                if not line:
                    continue
                chunk = json.loads(line.decode("utf-8"))
                if chunk.get("error"):
                    raise GenerationError(str(chunk["error"]))
                piece = (chunk.get("message") or {}).get("content") or ""
                if piece:
                    text.append(piece)
                    if on_text:
                        on_text(piece)
                if chunk.get("done"):
                    break
    except urllib.error.HTTPError as error:
        body = error.read().decode("utf-8", errors="replace")[:300]
        if error.code == 404:
            raise GenerationError(f"Ollama does not have the model '{payload.get('model')}'. "
                                  f"In a terminal: ollama pull {payload.get('model')}") from None
        raise GenerationError(f"Ollama error {error.code}: {body}") from None
    except (TimeoutError, socket.timeout):
        raise GenerationError(f"No answer from the model for {idle_s} s.") from None
    except (urllib.error.URLError, OSError) as error:
        raise GenerationError(f"Ollama does not answer at {url} ({getattr(error, 'reason', error)}). "
                              "Start it (tray icon, or 'ollama serve').") from None
    return "".join(text)


def models(url=OLLAMA_URL):
    """The models Ollama at *url* has, or None when it does not answer."""
    try:
        with _opener(url).open(f"{url.rstrip('/')}/api/tags", timeout=3) as reply:
            tags = json.loads(reply.read().decode("utf-8"))
    except Exception:
        return None
    return [m.get("name") or m.get("model") for m in tags.get("models", [])]


# -- reading the answer --------------------------------------------------------------------------

_PAIR = re.compile(r'"(\w+)"\s*:\s*(-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?|"(?:[^"\\]|\\.)*")\s*(?=[,}\]])')


class FieldWatcher:
    """Feeds a streamed JSON answer and reports every top-level value once it
    is complete: on_field(key, value)."""

    def __init__(self, on_field):
        self.on_field = on_field
        self.buffer = ""
        self.seen = {}

    def feed(self, chunk):
        self.buffer += chunk
        for m in _PAIR.finditer(self.buffer):
            key, raw = m.group(1), m.group(2)
            if key not in el.BY_KEY:
                continue
            value = json.loads(raw)
            if self.seen.get(key) != value:
                self.seen[key] = value
                self.on_field(key, value)


def parse(text):
    """The answer as a dict; tolerates a code fence or text round the object."""
    text = (text or "").strip()
    text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.M).strip()
    try:
        return json.loads(text)
    except ValueError:
        start, end = text.find("{"), text.rfind("}")
        if start >= 0 and end > start:
            try:
                return json.loads(text[start:end + 1])
            except ValueError:
                pass
    raise GenerationError("The model's answer is not JSON: " + text[:200])


def _split(answer):
    assumed = [a for a in answer.get("assumed") or [] if a in el.BY_KEY]
    notes = str(answer.get("notes") or "")
    return el.normalise(answer), assumed, notes


# -- the conversation ----------------------------------------------------------------------------

def _request(prompt, base):
    if base is None:
        return f"Describe this package:\n{prompt}"
    return (f"This is the current element:\n{json.dumps(el.normalise(base))}\n\n"
            f"Change it as follows, and keep every value the change does not concern:\n{prompt}\n"
            "List in \"assumed\" only values you had to choose for this change.")


def _repair_request(issues):
    lines = "\n".join(f"- {i.field or 'spec'}: {i.message}"
                      + (f" (e.g. {', '.join(f'{k} = {v}' for k, v in i.fix.items())})" if i.fix else "")
                      for i in issues)
    return (f"The spec does not build, these checks failed:\n{lines}\n"
            "Return the corrected spec, changing as little as possible.")


def generate(prompt, base=None, model=DEFAULT_MODEL, url=OLLAMA_URL, chat=None, on_field=None, on_text=None,
             max_repairs=MAX_REPAIRS, base_assumed=()):
    """
    A Draft for *prompt* (new element), or for changing *base* (a spec dict,
    whose assumed fields are *base_assumed*).

    *chat(url, payload, on_text)* -> answer text; default stream_chat.
    *on_field(key, value)* as values arrive; *on_text(chunk)* for raw text.
    """
    chat = chat or stream_chat
    messages = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": _request(prompt, base)}]
    draft = None
    for attempt in range(1 + max_repairs):
        watcher = FieldWatcher(on_field) if on_field else None

        def feed(chunk, watcher=watcher):
            if watcher:
                watcher.feed(chunk)
            if on_text:
                on_text(chunk)

        text = chat(url, {"model": model, "messages": messages, "format": el.json_schema(), "think": False,
                          "options": {"temperature": 0, "num_ctx": CONTEXT}, "keep_alive": "30m"}, feed)
        spec, assumed, notes = _split(parse(text))
        if base is not None:
            # a change keeps what was assumed before, unless the person has now set it
            kept = {k for k in base_assumed if k in spec and spec[k] == el.normalise(base).get(k)}
            assumed = sorted(set(assumed) | kept)
        issues = el.validate(spec)
        draft = Draft(spec, assumed, notes, issues, attempt + 1, model, prompt, text)
        if draft.ok:
            return draft
        messages += [{"role": "assistant", "content": text},
                     {"role": "user", "content": _repair_request(el.errors(issues))}]
    return draft
