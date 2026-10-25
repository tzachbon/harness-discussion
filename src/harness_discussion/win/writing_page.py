"""The Writing page of the Windows Settings window: the writer's sources, models, modes and shortcuts, and Dictation."""
import tkinter as tk
from copy import deepcopy
from tkinter import ttk

from .. import settings, writer
from .. import writer_settings as ws
from .sources_ui import CARD

PLAN_DEFAULTS = {k: settings.DEFAULTS[k] for k in ("writer", "writer_sources", "writer_models", "modes",
                                                   "default_mode", "rewrite_hotkey", "dictate_hotkey", "stt_sources",
                                                   "dictate_polish", "dictate_mode")}
LOCAL_MODEL = "Qwen3-4B"  # fixed: Local has no model or effort choice
FIXES = {"missing": "Install…", "signed_out": "Sign in…", "expired": "Sign in…", "not_downloaded": "Download…"}


def status_text(status):
    """Unlike voices, nothing may check a writer source yet, so no status reads as not checked, not as checking."""
    return "Not checked" if status is None else settings.status_text(status)


class WritingPage:
    """Mixed into SettingsWindow, which owns win, cfg, _card and _set."""

    def _writing_page(self, page, font):
        for key, value in PLAN_DEFAULTS.items():
            self.cfg.setdefault(key, value)
        card = self._card(page, "Writer")
        self.writer_on = tk.BooleanVar(self.win, self.cfg["writer"])
        self.writer_check = ttk.Checkbutton(card, text="Rewrite selected text in place", style="Card.TCheckbutton",
                                            variable=self.writer_on,
                                            command=lambda: self._set("writer", self.writer_on.get()))
        self.writer_check.pack(anchor="w")
        card = self._card(page, "Sources")
        frame = tk.Frame(card, bg=CARD)
        frame.pack(fill="x")
        self.writer_rows = {name: self._writer_row(frame, name) for name, _ in self.cfg["writer_sources"]}
        self._shortcut_row(card, "rewrite_hotkey", "Rewrites the selection with the default mode. "
                           "Press it twice to pick a mode.")
        self._modes_section(self._card(page, "Modes"), font)
        card = self._card(page, "Dictation")
        frame = tk.Frame(card, bg=CARD)
        frame.pack(fill="x")
        self.dictation_rows = {name: self._writer_row(frame, name, "stt_sources")
                               for name, _ in self.cfg["stt_sources"]}
        self.polish_on = tk.BooleanVar(self.win, self.cfg["dictate_polish"])
        self.polish_check = ttk.Checkbutton(card, text="Tidy dictation after transcribing",
                                            style="Card.TCheckbutton", variable=self.polish_on,
                                            command=self._polish_changed)
        self.polish_check.pack(anchor="w", pady=(8, 0))
        row = tk.Frame(card, bg=CARD)
        row.pack(fill="x", pady=(8, 0))
        ttk.Label(row, text="Dictation mode", style="Card.TLabel", width=14).pack(side="left")
        self.dictate_mode = ttk.Combobox(row, state="readonly", width=24)
        self.dictate_mode.bind("<<ComboboxSelected>>", lambda _e: self._set("dictate_mode", self.dictate_mode.get()))
        self.dictate_mode.pack(side="left")
        ttk.Label(card, text="Create or edit a custom prompt in Modes above, then select it here.",
                  style="Sub.Card.TLabel", wraplength=440).pack(anchor="w", pady=(6, 0))
        self.mic_status = ttk.Label(card, style="Sub.Card.TLabel")
        self.mic_status.pack(anchor="w", pady=(8, 0))
        self._shortcut_row(card, "dictate_hotkey", "Hold to record, release to type the text at the caret.")
        self.refresh_writing()

    def _modes_section(self, card, font):
        row = tk.Frame(card, bg=CARD)
        row.pack(fill="x")
        ttk.Label(row, text="Default mode", style="Card.TLabel", width=14).pack(side="left")
        self.default_mode = ttk.Combobox(row, state="readonly", width=24)
        self.default_mode.bind("<<ComboboxSelected>>", lambda _e: self._set("default_mode", self.default_mode.get()))
        self.default_mode.pack(side="left")
        row = tk.Frame(card, bg=CARD)
        row.pack(fill="x", pady=(12, 0))
        ttk.Label(row, text="Edit mode", style="Card.TLabel", width=14).pack(side="left")
        self.mode_pick = ttk.Combobox(row, state="readonly", width=24)
        self.mode_pick.bind("<<ComboboxSelected>>", lambda _e: self._edit_mode(self.mode_pick.get()))
        self.mode_pick.pack(side="left")
        self.mode_new = ttk.Button(row, text="New mode", command=lambda: self._edit_mode(None))
        self.mode_new.pack(side="right")
        self.mode_name = ttk.Entry(card, font=(font, 10))
        self.mode_name.pack(fill="x", pady=(8, 0))
        self.mode_text = tk.Text(card, height=4, width=40, wrap="word", font=(font, 10), relief="solid", bd=1)
        self.mode_text.pack(fill="x", pady=(6, 0))
        row = tk.Frame(card, bg=CARD)
        row.pack(fill="x", pady=(8, 0))
        self.mode_error = ttk.Label(row, style="Sub.Card.TLabel", foreground="#c42b1c")
        self.mode_error.pack(side="left")
        self.mode_delete = ttk.Button(row, text="Delete", command=self._delete_mode)
        self.mode_delete.pack(side="right")
        self.mode_save = ttk.Button(row, text="Save", command=self._save_mode)
        self.mode_save.pack(side="right", padx=(0, 8))
        ttk.Label(card, text="A mode with a built-in mode's name replaces it. Deleting it brings the built-in back.",
                  style="Sub.Card.TLabel", wraplength=440).pack(anchor="w", pady=(6, 0))
        self._edit_mode(self.cfg["default_mode"])

    def _modes(self):
        return writer.all_modes(self.cfg["modes"])

    def _edit_mode(self, name):
        """Loads a mode into the editor; None starts a new one."""
        modes = self._modes()
        names = [m.name for m in modes]
        mode = writer.find_mode(name, modes) if name is not None else None
        self._editing = mode and mode.name
        for box in (self.default_mode, self.mode_pick):
            box["values"] = names
        self.default_mode.set(writer.find_mode(self.cfg["default_mode"], modes).name)
        self.mode_pick.set(self._editing or "")
        self.mode_name.delete(0, "end")
        self.mode_name.insert(0, self._editing or "")
        self.mode_text.delete("1.0", "end")
        self.mode_text.insert("1.0", mode.instructions if mode else "")
        self.mode_delete["state"] = "normal" if mode and not mode.builtin else "disabled"
        self.mode_error["text"] = ""

    def _save_mode(self):
        name, instructions = self.mode_name.get().strip(), self.mode_text.get("1.0", "end").strip()
        if not name:
            self.mode_error["text"] = "Give the mode a name."
            return
        if not instructions:
            self.mode_error["text"] = "Write what the mode should do."
            return
        if name != self._editing and name in [m.name for m in self._modes()]:
            self.mode_error["text"] = f"A mode named {name} already exists."
            return
        custom = [{"name": m.name, "instructions": m.instructions} for m in self._modes() if not m.builtin]
        at = next((i for i, m in enumerate(custom) if m["name"] == self._editing), len(custom))
        renamed = at < len(custom) and self._editing != name  # editing a built-in leaves the built-in in place
        custom[at:at + 1] = [{"name": name, "instructions": instructions}]
        self._set("modes", custom)
        if renamed and self.cfg["default_mode"] == self._editing:
            self._set("default_mode", name)
        if renamed and self.cfg["dictate_mode"] == self._editing:
            self._set("dictate_mode", name)
        self._edit_mode(name)
        self._refresh_dictation_mode()

    def _delete_mode(self):
        self._set("modes", [{"name": m.name, "instructions": m.instructions}
                            for m in self._modes() if not m.builtin and m.name != self._editing])
        if self.cfg["default_mode"] not in [m.name for m in self._modes()]:
            self._set("default_mode", writer.BUILTIN_MODES[0].name)
        name = ws.default_mode(self.cfg["dictate_mode"], self.cfg["modes"], "Clean dictation")
        if name != self.cfg["dictate_mode"]:
            self._set("dictate_mode", name)
        self._edit_mode(self._editing)
        self._refresh_dictation_mode()

    def _polish_changed(self):
        self._set("dictate_polish", self.polish_on.get())
        self._refresh_dictation_mode()

    def _refresh_dictation_mode(self):
        self.dictate_mode.configure(values=[m.name for m in self._modes()],
                                   state="readonly" if self.cfg["dictate_polish"] else "disabled")
        self.dictate_mode.set(ws.default_mode(self.cfg["dictate_mode"], self.cfg["modes"], "Clean dictation"))

    def _writer_row(self, f, name, key="writer_sources"):
        on = tk.BooleanVar(self.win)
        model, effort = ttk.Combobox(f, state="readonly", width=13), ttk.Combobox(f, state="readonly", width=6)
        model.bind("<<ComboboxSelected>>", lambda _e: self._writer_model_changed(name))
        effort.bind("<<ComboboxSelected>>", lambda _e: self._writer_model_changed(name, effort.get()))
        return {"on": on, "model": model, "effort": effort,
                "toggle": ttk.Checkbutton(f, text=name, style="Card.TCheckbutton", variable=on, width=7,
                                          command=lambda: self._writer_toggled(name, key)),
                "status": ttk.Label(f, style="Sub.Card.TLabel", width=10, wraplength=76),
                "fix": ttk.Button(f, width=8, command=lambda: self.on_writer_fix(name)),
                "up": ttk.Button(f, text="↑", width=2, command=lambda: self._writer_move(name, -1, key)),
                "down": ttk.Button(f, text="↓", width=2, command=lambda: self._writer_move(name, 1, key))}

    def _show_writer_models(self, name, row):
        """The model and effort menus show the app's cached catalog; with none, they say so and stay off."""
        model, effort = row["model"], row["effort"]
        catalog = row["catalog"] = deepcopy(self.writer_catalogs().get(name) or [])
        chosen = self.cfg["writer_models"].get(name) or {}
        found = next((m for m in catalog if m["id"] == chosen.get("model")), None)
        if name == "Local" or not catalog:
            model.configure(values=[LOCAL_MODEL if name == "Local" else "Models not loaded"], state="disabled")
            model.current(0)
        else:
            model.configure(values=["Default"] + [m.get("label") or m["id"] for m in catalog], state="readonly")
            model.current(catalog.index(found) + 1 if found else 0)
        efforts = (found or {}).get("efforts") or []
        effort.configure(values=efforts, state="readonly" if efforts else "disabled")
        effort.set(chosen.get("effort") or "" if efforts else "")

    def _writer_model_changed(self, name, effort=None):
        row = self.writer_rows[name]
        catalog = row["catalog"]
        index = row["model"].current() - 1
        models = {k: v for k, v in self.cfg["writer_models"].items() if k != name}
        if index >= 0:
            picked = catalog[index]
            effort = effort if effort in (picked.get("efforts") or []) else picked.get("default_effort")
            models[name] = {"model": picked["id"], "effort": effort}
        self._set("writer_models", models)
        self._show_writer_models(name, row)
        row["model"].selection_clear()
        row["effort"].selection_clear()

    def _show_rows(self, key, rows, columns):
        sources, statuses = self.cfg[key], self.writer_statuses()
        for i, (name, enabled) in enumerate(sources):
            row = rows[name]
            row["on"].set(enabled)
            for column, widget in enumerate(columns):
                row[widget].grid(row=i, column=column, sticky="w", padx=(0, 6), pady=2)
            row["up"]["state"] = "normal" if i else "disabled"
            row["down"]["state"] = "normal" if i < len(sources) - 1 else "disabled"
            row["status"]["text"] = status_text(statuses.get(name))
            row["fix"]["text"] = FIXES.get(statuses.get(name), "")
            if "fix" not in columns or not row["fix"]["text"]:
                row["fix"].grid_remove()

    def refresh_writing(self):
        """Shows the statuses, model lists and microphone state the app last cached. Never checks anything itself."""
        for name, row in self.writer_rows.items():
            self._show_writer_models(name, row)
        self._show_rows("writer_sources", self.writer_rows, ("toggle", "status", "model", "effort", "up", "down", "fix"))
        self._show_rows("stt_sources", self.dictation_rows, ("toggle", "up", "down"))  # no STT status is supplied
        self.mic_status["text"] = f"Microphone: {self.dictate_status()}"
        self._refresh_dictation_mode()

    def _writer_toggled(self, name, key):
        rows = self.writer_rows if key == "writer_sources" else self.dictation_rows
        on = rows[name]["on"].get()
        self._set(key, [[n, on if n == name else was] for n, was in self.cfg[key]])
        self.refresh_writing()

    def _writer_move(self, name, step, key):
        sources = [list(s) for s in self.cfg[key]]
        i = [n for n, _ in sources].index(name)
        if 0 <= i + step < len(sources):
            sources[i], sources[i + step] = sources[i + step], sources[i]
            self._set(key, sources)
            self.refresh_writing()
