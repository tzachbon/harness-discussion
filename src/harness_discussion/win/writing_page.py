"""The Writing and Dictation pages of the Windows Settings window, organized like macOS."""
import tkinter as tk
from copy import deepcopy
from tkinter import messagebox, ttk

from .. import mic, settings, writer
from .. import writer_settings as ws
from .sources_ui import CARD

PLAN_DEFAULTS = {k: settings.DEFAULTS[k] for k in ("writer", "writer_sources", "writer_models", "modes",
                                                   "default_mode", "rewrite_hotkey", "dictate_hotkey", "stt_sources",
                                                   "dictate_polish", "dictate_mode", "dictate_local_tidy",
                                                   "dictate_microphone")}
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
        for key in ("live_check", "live_off_apps"):
            self.cfg.setdefault(key, settings.DEFAULTS[key])
        card = self._card(page, "Rewrite")
        self.writer_on = tk.BooleanVar(self.win, self.cfg["writer"])
        self.writer_check = ttk.Checkbutton(card, text="Rewrite selected text", style="Card.TCheckbutton",
                                            variable=self.writer_on, command=self._writer_changed)
        self.writer_check.pack(anchor="w")
        ttk.Label(card, text="Adds ✎ to the bar over text you can edit, and the shortcut.",
                  style="Sub.Card.TLabel", wraplength=440).pack(anchor="w", pady=(4, 0))
        self.live_on = tk.BooleanVar(self.win, self.cfg["live_check"])
        self.live_check = ttk.Checkbutton(card, text="Check as I type", style="Card.TCheckbutton",
                                          variable=self.live_on, command=self._live_changed)
        self.live_check.pack(anchor="w", pady=(12, 0))
        ttk.Label(card, text="When you pause, underlines the default mode's changes to the line you are typing. "
                  "The rewrite shortcut with nothing selected applies them all. Never in password fields.",
                  style="Sub.Card.TLabel", wraplength=440).pack(anchor="w", pady=(4, 0))
        self.live_off_frame = tk.Frame(card, bg=CARD)
        self.live_off_frame.pack(fill="x")
        self._show_live_off()
        row = tk.Frame(card, bg=CARD)
        row.pack(fill="x", pady=(12, 0))
        ttk.Label(row, text="Default mode", style="Card.TLabel", width=14).pack(side="left")
        self.default_mode = ttk.Combobox(row, state="readonly", width=24)
        self.default_mode.bind("<<ComboboxSelected>>", lambda _e: self._set("default_mode", self.default_mode.get()))
        self.default_mode.pack(side="left")
        self._shortcut_row(card, "rewrite_hotkey", "Rewrites the selection with the default mode. "
                           "Press it twice to pick a mode.")
        card = self._card(page, "Sources")
        frame = tk.Frame(card, bg=CARD)
        frame.pack(fill="x")
        self.writer_rows = {name: self._writer_row(frame, name) for name, _ in self.cfg["writer_sources"]}
        ttk.Label(card, text="Sources rewrite top to bottom. If one fails, the next one tries. Cursor and Codex "
                  "send the selected text to their service. Local keeps it on this PC.",
                  style="Sub.Card.TLabel", wraplength=440).pack(anchor="w", pady=(8, 0))
        self._modes_section(self._card(page, "Modes"), font)

    def _writer_changed(self):
        self._set("writer", self.writer_on.get())
        self.live_check.state(["!disabled"] if self.writer_on.get() else ["disabled"])

    def _live_changed(self):
        if self.live_on.get():
            names = [n for n, on in self.cfg["writer_sources"] if on] or ["no source"]
            ok = messagebox.askokcancel(
                "Check as I type", ws.live_confirm_text(names, "this PC"),
                icon="question", parent=self.win)
            if not ok:
                self.live_on.set(False)
                return
        self._set("live_check", self.live_on.get())

    def _show_live_off(self):
        """One row per app the user turned checking off for, each with Turn on."""
        for child in self.live_off_frame.winfo_children():
            child.destroy()
        self.live_check.state(["!disabled"] if self.cfg["writer"] else ["disabled"])
        for name in self.cfg["live_off_apps"]:
            row = tk.Frame(self.live_off_frame, bg=CARD)
            row.pack(fill="x", pady=(6, 0))
            ttk.Label(row, text=f"Off in {name}", style="Card.TLabel").pack(side="left")
            ttk.Button(row, text="Turn on", command=lambda name=name: self._live_turn_on(name)).pack(side="right")

    def _live_turn_on(self, name):
        self._set("live_off_apps", [n for n in self.cfg["live_off_apps"] if n != name])
        self._show_live_off()

    def _dictation_page(self, page):
        card = self._card(page, "Sources")
        frame = tk.Frame(card, bg=CARD)
        frame.pack(fill="x")
        self.dictation_rows = {name: self._writer_row(frame, name, "stt_sources")
                               for name, _ in self.cfg["stt_sources"]}
        ttk.Label(card, text="Sources transcribe top to bottom. If one fails, the next one tries.",
                  style="Sub.Card.TLabel", wraplength=440).pack(anchor="w", pady=(8, 0))
        card = self._card(page, "Tidy")
        self.polish_on = tk.BooleanVar(self.win, self.cfg["dictate_polish"])
        self.polish_check = ttk.Checkbutton(card, text="Tidy dictation after transcribing",
                                            style="Card.TCheckbutton", variable=self.polish_on,
                                            command=self._polish_changed)
        self.polish_check.pack(anchor="w")
        self.local_tidy_on = tk.BooleanVar(self.win, self.cfg["dictate_local_tidy"])
        self.local_tidy_check = ttk.Checkbutton(card, text="Prefer local tidy for faster results",
                style="Card.TCheckbutton", variable=self.local_tidy_on,
                command=lambda: self._set("dictate_local_tidy", self.local_tidy_on.get()))
        self.local_tidy_check.pack(anchor="w", pady=(6, 0))
        ttk.Label(card, text="Uses Writing → Local when downloaded and enabled. Otherwise uses your writing sources.",
                  style="Sub.Card.TLabel", wraplength=440).pack(anchor="w", pady=(4, 0))
        ttk.Label(card, text="Off types exactly what was heard, sooner.", style="Sub.Card.TLabel").pack(
            anchor="w", pady=(4, 0))
        row = tk.Frame(card, bg=CARD)
        row.pack(fill="x", pady=(12, 0))
        ttk.Label(row, text="Dictation mode", style="Card.TLabel", width=14).pack(side="left")
        self.dictate_mode = ttk.Combobox(row, state="readonly", width=24)
        self.dictate_mode.bind("<<ComboboxSelected>>", lambda _e: self._set("dictate_mode", self.dictate_mode.get()))
        self.dictate_mode.pack(side="left")
        ttk.Label(card, text="Create or edit a custom prompt in Writing → Modes, then select it here.",
                  style="Sub.Card.TLabel", wraplength=440).pack(anchor="w", pady=(6, 0))
        card = self._card(page, "Shortcut and microphone")
        self._shortcut_row(card, "dictate_hotkey", "Hold to record, release to type the text at the caret.")
        row = tk.Frame(card, bg=CARD)
        row.pack(fill="x", pady=(12, 0))
        ttk.Label(row, text="Input device", style="Card.TLabel", width=14).pack(side="left")
        self._mic_devices = ["", *mic.input_devices()]
        chosen = self.cfg["dictate_microphone"]
        if chosen not in self._mic_devices:
            self._mic_devices.append(chosen)
        self.microphone = ttk.Combobox(row, state="readonly", width=30,
                                      values=["System default", *self._mic_devices[1:]])
        self.microphone.current(self._mic_devices.index(chosen))
        self.microphone.bind("<<ComboboxSelected>>", lambda _e: self._set(
            "dictate_microphone", self._mic_devices[self.microphone.current()]))
        self.microphone.pack(side="left")
        row = tk.Frame(card, bg=CARD)
        row.pack(fill="x", pady=(12, 0))
        self.mic_status = ttk.Label(row, style="Card.TLabel")
        self.mic_status.pack(side="left")
        ttk.Button(row, text="Microphone settings", command=self.on_mic_settings).pack(side="right")
        ttk.Label(card, text="A short tap does nothing. Release to transcribe, tidied with the dictation mode when "
                  "that is on. Local keeps audio on this PC. Enabled Cursor or Codex sources receive audio if "
                  "earlier sources fail. Audio is not saved.",
                  style="Sub.Card.TLabel", wraplength=440).pack(anchor="w", pady=(8, 0))
        self.refresh_writing()

    def _modes_section(self, card, font):
        row = tk.Frame(card, bg=CARD)
        row.pack(fill="x")
        ttk.Label(row, text="Edit mode", style="Card.TLabel", width=14).pack(side="left")
        self.mode_pick = ttk.Combobox(row, state="readonly", width=24)
        self.mode_pick.bind("<<ComboboxSelected>>", lambda _e: self._edit_mode(self.mode_pick.get()))
        self.mode_pick.pack(side="left")
        self.mode_new = ttk.Button(row, text="New mode", command=self._add_mode)
        self.mode_new.pack(side="right")
        self.mode_name = ttk.Entry(card, font=(font, 10))
        self.mode_name.pack(fill="x", pady=(8, 0))
        self.mode_text = tk.Text(card, height=4, width=40, wrap="word", font=(font, 10), relief="solid", bd=1)
        self.mode_text.pack(fill="x", pady=(6, 0))
        row = tk.Frame(card, bg=CARD)
        row.pack(fill="x", pady=(8, 0))
        self.mode_status = ttk.Label(row, style="Sub.Card.TLabel", wraplength=300)
        self.mode_status.pack(side="left")
        self.mode_delete = ttk.Button(row, text="Delete", command=self._delete_mode)
        self.mode_delete.pack(side="right")
        self.mode_save = ttk.Button(row, text="Save", command=self._save_mode)
        self.mode_save.pack(side="right", padx=(0, 8))
        ttk.Label(card, text="Editing a built-in mode saves your version under the same name. Reset brings the "
                  "built-in back.", style="Sub.Card.TLabel", wraplength=440).pack(anchor="w", pady=(6, 0))
        self._editing, self._loaded = self.cfg["default_mode"], None

    def _modes(self):
        return writer.all_modes(self.cfg["modes"])

    def _refresh_modes(self, reload=False):
        """Menus follow the saved modes. The editor keeps an unsaved draft unless its mode changed or `reload`."""
        names = [m.name for m in self._modes()]
        self.default_mode.configure(values=names)
        self.default_mode.set(ws.default_mode(self.cfg["default_mode"], self.cfg["modes"]))
        self._refresh_dictation_mode()
        if self._editing not in names:
            self._editing = ws.default_mode(self.cfg["default_mode"], self.cfg["modes"])
        self.mode_pick.configure(values=names)
        self.mode_pick.set(self._editing)
        if reload or self._editing != self._loaded:
            self._edit_mode(self._editing)

    def _mode_message(self, text, error=False):
        self.mode_status.configure(text=text, foreground="#c42b1c" if error else "#5f5f5f")

    def _edit_mode(self, name):
        """Loads a mode into the editor. Built-in modes keep their name."""
        self._editing = self._loaded = name
        mode = writer.find_mode(name, self._modes())
        builtin = name in {m.name for m in writer.BUILTIN_MODES}
        self.mode_pick.set(name)
        self.mode_name.configure(state="normal")
        self.mode_name.delete(0, "end")
        self.mode_name.insert(0, mode.name)
        self.mode_name.configure(state="disabled" if builtin else "normal")
        self.mode_text.delete("1.0", "end")
        self.mode_text.insert("1.0", mode.instructions)
        overridden = any(m.get("name") == name for m in self.cfg["modes"] if isinstance(m, dict))
        self.mode_delete["text"] = "Reset" if builtin else "Delete"
        self.mode_delete["state"] = "normal" if overridden or not builtin else "disabled"
        self._mode_message("Built-in modes keep their name." if builtin else "")

    def _set_modes(self, modes, editing):
        self._set("modes", modes)
        for key, fallback in (("default_mode", "Fix errors"), ("dictate_mode", "Clean dictation")):
            name = ws.default_mode(self.cfg[key], modes, fallback)
            if name != self.cfg[key]:
                self._set(key, name)
        self._editing = editing
        self._refresh_modes(reload=True)

    def _custom_modes(self):
        return [{"name": m.name, "instructions": m.instructions} for m in self._modes() if not m.builtin]

    def _add_mode(self):
        self._set_modes(*ws.add_mode(self._custom_modes()))

    def _save_mode(self):
        old = self._editing
        modes, name, error = ws.save_mode(self._custom_modes(), old, self.mode_name.get(),
                                          self.mode_text.get("1.0", "end"))
        if error:
            self._mode_message(error, True)
            return
        for key in ("default_mode", "dictate_mode"):
            if self.cfg[key] == old:
                self._set(key, name)
        self._set_modes(modes, name)
        self._mode_message("Saved.")

    def _delete_mode(self):
        self._set_modes(ws.delete_mode(self._custom_modes(), self._editing), self._editing)

    def _polish_changed(self):
        self._set("dictate_polish", self.polish_on.get())
        self._refresh_dictation_mode()

    def _refresh_dictation_mode(self):
        self.local_tidy_on.set(self.cfg["dictate_local_tidy"])
        self.local_tidy_check.configure(state="normal" if self.cfg["dictate_polish"] else "disabled")
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
                "fix": ttk.Button(f, width=8, command=lambda: self.on_writer_fix(name, True)
                                  if key == "stt_sources" else self.on_writer_fix(name)),
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
            model.configure(values=[ws.default_label(name)] + [m.get("label") or m["id"] for m in catalog], state="readonly")
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
        sources = self.cfg[key]
        statuses = self.stt_statuses() if key == "stt_sources" else self.writer_statuses()
        for i, (name, enabled) in enumerate(sources):
            row = rows[name]
            row["on"].set(enabled)
            for column, widget in enumerate(columns):
                row[widget].grid(row=i, column=column, sticky="w", padx=(0, 6), pady=2)
            row["up"]["state"] = "normal" if i else "disabled"
            row["down"]["state"] = "normal" if i < len(sources) - 1 else "disabled"
            row["status"]["text"] = status_text(statuses.get(name))
            row["fix"]["text"] = ("Download…" if name == "Local" and statuses.get(name) == "missing"
                                    else FIXES.get(statuses.get(name), ""))
            if "fix" not in columns or not row["fix"]["text"]:
                row["fix"].grid_remove()

    def refresh_writing(self):
        """Shows the statuses, model lists and microphone state the app last cached. Never checks anything itself."""
        for name, row in self.writer_rows.items():
            self._show_writer_models(name, row)
        self._show_rows("writer_sources", self.writer_rows, ("toggle", "status", "model", "effort", "up", "down", "fix"))
        self._show_rows("stt_sources", self.dictation_rows, ("toggle", "status", "up", "down", "fix"))
        self.mic_status["text"] = f"Microphone: {self.dictate_status()}"
        self.live_on.set(self.cfg["live_check"])
        self._show_live_off()
        self._refresh_modes()

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
