# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
The EM assistant panel: a chat about the EM setup of the open package.

A local model answers in seconds on a GPU and in up to a minute on a CPU,
and a cloud model is a network round trip per tool call, so the
conversation runs in a worker thread and FreeCAD stays usable. What it may
not do from there is change anything: a tool call edits the project and,
through it, the simulation document, and FreeCAD documents belong to the GUI
thread. Every tool call is therefore handed to the GUI thread and the worker
waits for its answer (_MainThreadRunner).

Providers: a local model through Ollama (free, nothing leaves the computer),
or Claude / Gemini with the person's own API key. With a cloud model the
project description — layer names, materials, sizes, checks — is sent to
that provider; the panel says so before the first question.
"""

import FreeCAD
from PySide import QtCore, QtGui, QtWidgets

from . import em_preferences as preferences, em_state as state

_PROVIDERS = (("off", "Off"), ("ollama", "Local model (Ollama)"),
              ("claude", "Claude (your API key)"), ("gemini", "Gemini (your API key)"))


class _MainThreadRunner(QtCore.QObject):
    """Runs the ToolBox on the GUI thread; called from the worker, it blocks until done."""

    request = QtCore.Signal(object)

    def __init__(self, toolbox):
        super().__init__()
        self.toolbox = toolbox
        self.changed = False
        self.request.connect(self._run, QtCore.Qt.BlockingQueuedConnection)

    def _run(self, box):
        name, args, answer = box
        try:
            answer.append(self.toolbox.run(name, args))
        except Exception as exc:          # never leave the worker waiting
            answer.append((f"error: {exc}", False))

    def __call__(self, name, args):
        if QtCore.QThread.currentThread() is self.thread():
            result = self.toolbox.run(name, args)
        else:
            answer = []
            self.request.emit((name, args, answer))
            result = answer[0]
        self.changed = self.changed or result[1]
        return result


class _Worker(QtCore.QThread):
    answered = QtCore.Signal(object)
    progress = QtCore.Signal(str, int)      # emitted from this thread, shown on the GUI thread

    def __init__(self, assistant, question):
        super().__init__()
        self.assistant, self.question = assistant, question
        self.assistant.progress = lambda kind, count: self.progress.emit(kind, count)

    def run(self):
        try:
            events = self.assistant.ask(self.question)
        except Exception as exc:
            from ..app.assistant import Event
            events = [Event("error", f"The assistant failed: {exc}")]
        self.assistant.progress = None
        self.answered.emit(events)


class _Check(QtCore.QThread):
    """app.assistant.check_ollama in the background: loading a model takes a while."""
    done = QtCore.Signal(bool, str)

    def __init__(self, url, model):
        super().__init__()
        self.url, self.model = url, model

    def run(self):
        from ..app.assistant import check_ollama
        try:
            ok, text = check_ollama(self.url, self.model)
        except Exception as exc:
            ok, text = False, f"Test failed: {exc}"
        self.done.emit(ok, text)


class EMAssistantPanel(QtWidgets.QDockWidget):
    def __init__(self, parent=None):
        super().__init__("EM Assistant", parent)
        self.setObjectName("PS_EMAssistantPanel")
        self._assistant = None
        self._project = None
        self._worker = None
        self._runner = None
        self._check = None
        self._build_ui()
        self._load_preferences()
        state.add_listener(self._project_changed)

    # ── layout ───────────────────────────────────────────────────────────────

    def _build_ui(self):
        body = QtWidgets.QWidget()
        root = QtWidgets.QVBoxLayout(body)
        self.setWidget(body)

        form = QtWidgets.QFormLayout()
        self.provider = QtWidgets.QComboBox()
        for key, label in _PROVIDERS:
            self.provider.addItem(label, key)
        self.provider.currentIndexChanged.connect(self._provider_changed)
        form.addRow("Model:", self.provider)
        self.model = QtWidgets.QComboBox()
        self.model.setEditable(True)
        self.model.setToolTip("Any model name the provider knows; for Ollama the name you pulled")
        name_row = QtWidgets.QHBoxLayout()
        name_row.addWidget(self.model, 1)
        self.test_button = QtWidgets.QPushButton("Test")
        self.test_button.setToolTip("Check that Ollama runs, that the model is there, and how long one "
                                    "short answer takes (the first one also loads the model)")
        self.test_button.clicked.connect(self.test_ollama)
        name_row.addWidget(self.test_button)
        form.addRow("Name:", name_row)
        self.ollama_url = QtWidgets.QLineEdit()
        self.ollama_url.editingFinished.connect(self._provider_changed)
        form.addRow("Ollama URL:", self.ollama_url)
        keys = QtWidgets.QHBoxLayout()
        self.api_key = QtWidgets.QLineEdit()
        self.api_key.setEchoMode(QtWidgets.QLineEdit.Password)
        self.remember = QtWidgets.QCheckBox("remember")
        self.remember.setToolTip("Store the key in FreeCAD's user.cfg (a plain text file in your FreeCAD "
                                 "user folder). Unticked, it is kept for this session only.")
        keys.addWidget(self.api_key, 1)
        keys.addWidget(self.remember)
        self.key_row = QtWidgets.QWidget()
        self.key_row.setLayout(keys)
        form.addRow("API key:", self.key_row)
        root.addLayout(form)

        self.notice = QtWidgets.QLabel("")
        self.notice.setWordWrap(True)
        self.notice.setStyleSheet("color: #5a6b7d;")
        root.addWidget(self.notice)

        self.chat = QtWidgets.QTextBrowser()
        self.chat.setOpenExternalLinks(True)
        root.addWidget(self.chat, 1)

        row = QtWidgets.QHBoxLayout()
        self.question = QtWidgets.QLineEdit()
        self.question.setPlaceholderText("e.g. Which layers were guessed? Use Palace from 1 to 40 GHz.")
        self.question.returnPressed.connect(self.ask)
        self.send = QtWidgets.QPushButton("Ask")
        self.send.clicked.connect(self.ask)
        clear = QtWidgets.QPushButton("New chat")
        clear.clicked.connect(self.reset)
        row.addWidget(self.question, 1)
        row.addWidget(self.send)
        row.addWidget(clear)
        root.addLayout(row)

    def _load_preferences(self):
        provider = preferences.get("Provider")
        index = max(0, self.provider.findData(provider))
        self.provider.setCurrentIndex(index)
        self.ollama_url.setText(preferences.get("OllamaUrl"))
        if preferences.get("Model"):
            self.model.setCurrentText(preferences.get("Model"))
        self._provider_changed()

    def _provider_key(self):
        return self.provider.currentData()

    def _provider_changed(self, *_):
        from ..app import assistant
        provider = self._provider_key()
        current = self.model.currentText()
        self.model.clear()
        if provider == "claude":
            self.model.addItems(list(assistant.CLAUDE_MODELS))
            self.notice.setText("The project description (layer names, materials, sizes, checks) and your "
                                "questions go to Anthropic. Usage is billed to your key.")
        elif provider == "gemini":
            self.model.addItems(list(assistant.GEMINI_MODELS))
            self.notice.setText("The project description (layer names, materials, sizes, checks) and your "
                                "questions go to Google. Usage is billed to your key, or its free quota.")
        elif provider == "ollama":
            found = assistant.ollama_models(self.ollama_url.text() or assistant.OLLAMA_URL)
            if found:
                self.model.addItems(found)
                self.notice.setText("Runs on this computer; nothing leaves it.")
            else:
                self.model.addItems([assistant.OLLAMA_DEFAULT_MODEL, "qwen3:4b", "qwen3-coder:30b"])
                self.notice.setText("Ollama does not answer at that URL. Install it from ollama.com, start it, "
                                    "and pull a model, e.g. 'ollama pull qwen3:8b'.")
        else:
            self.notice.setText("The assistant is off.")
        self.notice.setStyleSheet("color: #5a6b7d;")
        if current and self.model.findText(current) >= 0:
            self.model.setCurrentText(current)
        self.key_row.setEnabled(provider in ("claude", "gemini"))
        self.ollama_url.setEnabled(provider == "ollama")
        self.test_button.setVisible(provider == "ollama")
        self.api_key.setText(preferences.api_key(provider) if provider in ("claude", "gemini") else "")
        self.remember.setChecked(preferences.key_is_remembered(provider))
        self.question.setEnabled(provider != "off")
        self.send.setEnabled(provider != "off")
        self._assistant = None

    def test_ollama(self):
        if self._check is not None:
            return
        from ..app import assistant
        url = self.ollama_url.text().strip() or assistant.OLLAMA_URL
        model = self.model.currentText().strip() or assistant.OLLAMA_DEFAULT_MODEL
        self.test_button.setEnabled(False)
        self.test_button.setText("…")
        self.notice.setText(f"Testing {model} at {url} … (the first answer loads the model, "
                            "on a CPU that can take a minute)")
        self._check = _Check(url, model)
        self._check.done.connect(self._tested)
        self._check.start()

    def _tested(self, ok, text):
        self._check = None
        self.test_button.setEnabled(True)
        self.test_button.setText("Test")
        self.notice.setText(("✔ " if ok else "✖ ") + text)
        self.notice.setStyleSheet("color: #2e7d32;" if ok else "color: #c0392b;")
        self._say("text" if ok else "error", text)

    def _project_changed(self):
        project = state.project_for(FreeCAD.ActiveDocument) or self._project_of_any()
        if project is not self._project:
            self._project = project
            self._assistant = None

    @staticmethod
    def _project_of_any():
        for name in FreeCAD.listDocuments():
            project = state.project_for(FreeCAD.getDocument(name))
            if project is not None:
                return project
        return None

    # ── conversation ─────────────────────────────────────────────────────────

    def reset(self):
        self._assistant = None
        self.chat.clear()

    def _make_assistant(self, project):
        from ..app import assistant
        provider = self._provider_key()
        key = self.api_key.text().strip()
        if provider in ("claude", "gemini"):
            preferences.set_api_key(provider, key, self.remember.isChecked())
        preferences.put("Provider", provider)
        preferences.put("Model", self.model.currentText().strip())
        preferences.put("OllamaUrl", self.ollama_url.text().strip())
        toolbox = assistant.make_toolbox(project)
        self._runner = _MainThreadRunner(toolbox)
        return assistant.Assistant(provider, toolbox, model=self.model.currentText().strip(), api_key=key,
                                   ollama_url=self.ollama_url.text().strip() or assistant.OLLAMA_URL,
                                   tool_runner=self._runner)

    def ask(self):
        question = self.question.text().strip()
        if not question or self._worker is not None:
            return
        self._project_changed()
        project = self._project
        if project is None:
            self._say("error", "Build the EM model first (EM Simulation panel: Build model, or Make simulation package).")
            return
        if self._assistant is None or self._assistant.toolbox.project is not project:
            try:
                self._assistant = self._make_assistant(project)
            except ValueError as exc:
                self._say("error", str(exc))
                return
        self._runner.changed = False
        self._say("you", question)
        self.question.clear()
        self.send.setEnabled(False)
        self.send.setText("…")
        self._worker = _Worker(self._assistant, question)
        self._worker.answered.connect(self._answered)
        self._worker.progress.connect(self._progress)
        self._worker.start()

    def _progress(self, kind, count):
        if self._worker is not None:
            self.send.setText(f"thinking {count}" if kind == "thinking" else f"writing {count}")

    def _answered(self, events):
        for event in events:
            if event.kind == "tool":
                mark = "⚙" if event.changed else "🔎"
                first = event.text.splitlines()[0] if event.text else ""
                self._say("tool", f"{mark} {event.tool}: {first}")
            else:
                self._say(event.kind, event.text)
        self._worker = None
        self.send.setEnabled(True)
        self.send.setText("Ask")
        if self._runner is not None and self._runner.changed:
            state.changed()

    def _say(self, who, text):
        colours = {"you": "#1f3b63", "text": "#000000", "tool": "#5a6b7d", "error": "#c0392b"}
        prefix = {"you": "<b>You:</b> ", "text": "<b>Assistant:</b> ", "tool": "", "error": "<b>⚠</b> "}
        safe = (text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("\n", "<br>"))
        self.chat.append(f'<div style="color:{colours.get(who, "#000")}; margin-bottom:6px">'
                         f'{prefix.get(who, "")}{safe}</div>')
        self.chat.moveCursor(QtGui.QTextCursor.End)
