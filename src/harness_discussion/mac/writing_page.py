"""Settings → Writing and Dictation on macOS: sources with models, the default mode, modes, and shortcuts."""
import AppKit

from .. import settings, writer
from .. import writer_settings as ws
from . import ui

LOCAL_TEXT = {"missing": "Not downloaded", "unsupported": "Not available on this Mac"}
MIC_TEXT = {"ready": "Allowed", "not_asked": "Asks the first time you dictate",
            "denied": "Not allowed. Turn it on in System Settings."}


def _popup(fn):
    p = AppKit.NSPopUpButton.alloc().init()
    p.setControlSize_(AppKit.NSControlSizeSmall)
    return ui.bind(p, lambda s: fn(s.indexOfSelectedItem()))


def _fill(popup, titles, selected):
    popup.removeAllItems()
    popup.addItemsWithTitles_(titles)
    popup.selectItemAtIndex_(max(0, selected))


class WritingPage:
    """Mixed into SettingsWindow, which owns app, cfg, _set, _fit and the shortcut rows."""

    def _writing_page(self):
        from .settings_ui import WIDTH, _card, _divider, _note, _spacer  # settings_ui imports this module
        on, _ = ui.option("Rewrite selected text", "Adds ✎ to the bar over text you can edit, and the shortcut.",
                          self.cfg["writer"], lambda v: self._set("writer", v), WIDTH)
        self.default_popup = _popup(self._default_picked)
        default = ui.stack([ui.label("Default mode", 13), _spacer(), self.default_popup], vertical=False)
        default.widthAnchor().constraintEqualToConstant_(WIDTH).setActive_(True)
        self.writer_rows = {name: self._writer_row(name, False) for name in settings.WRITER_SOURCES}
        self.writer_list = ui.stack([], spacing=10)

        self.mode_popup = _popup(lambda i: self._edit_mode(self._mode_list()[i].name))
        self.mode_name = AppKit.NSTextField.alloc().init()
        self.mode_name.widthAnchor().constraintEqualToConstant_(WIDTH - 90).setActive_(True)
        self.mode_text = AppKit.NSTextField.alloc().init()
        self.mode_text.setUsesSingleLineMode_(False)
        self.mode_text.cell().setWraps_(True)
        self.mode_text.widthAnchor().constraintEqualToConstant_(WIDTH - 90).setActive_(True)
        self.mode_text.heightAnchor().constraintEqualToConstant_(54).setActive_(True)
        self.mode_delete = ui.button("Delete", self._delete_mode)
        self.mode_status = ui.label("", 11, secondary=True, width=WIDTH - 150)
        modes = [ui.stack([self.mode_popup, _spacer(), ui.button("Add", self._add_mode), self.mode_delete],
                          vertical=False),
                 ui.stack([ui.label("Name", 12, width=80), self.mode_name], vertical=False),
                 ui.stack([ui.label("Instructions", 12, width=80), self.mode_text], vertical=False),
                 ui.stack([self.mode_status, _spacer(), ui.button("Save", self._save_mode, primary=True)],
                          vertical=False)]
        for row in (modes[0], modes[3]):
            row.widthAnchor().constraintEqualToConstant_(WIDTH).setActive_(True)
        self._editing, self._loaded = self.cfg["default_mode"], None
        return [_card([on, _divider(), default, _divider(), self._shortcut_row("rewrite_hotkey")]),
                ui.label("Sources", 13, bold=True), _card([self.writer_list]),
                _note("Sources rewrite top to bottom. If one fails, the next one tries. Cursor and Codex send "
                      "the selected text to their service. Local keeps it on this Mac."),
                ui.label("Modes", 13, bold=True), _card(modes)]

    def _dictation_page(self):
        from .settings_ui import WIDTH, _card, _divider, _note, _spacer
        self.stt_rows = {name: self._writer_row(name, True) for name in settings.STT_SOURCES}
        self.stt_list = ui.stack([], spacing=10)
        self.mic_status = ui.label("", 11, secondary=True)
        mic = ui.stack([ui.stack([ui.label("Microphone", 13), self.mic_status], spacing=1), _spacer(),
                        ui.button("Open Settings", self.app.host.open_mic_settings)], vertical=False)
        mic.widthAnchor().constraintEqualToConstant_(WIDTH).setActive_(True)
        polish, self.polish_switch = ui.option("Tidy dictation after transcribing", "Off types exactly what was heard, sooner.",
                              self.cfg["dictate_polish"], self._polish_changed, WIDTH)
        self.dictate_popup = _popup(lambda i: self._set("dictate_mode", self._mode_list()[i].name))
        self.dictate_popup.setAccessibilityLabel_("Dictation mode")
        mode = ui.stack([ui.label("Dictation mode", 13), _spacer(), self.dictate_popup], vertical=False)
        mode.widthAnchor().constraintEqualToConstant_(WIDTH).setActive_(True)
        return [_card([self.stt_list]), _card([polish, _divider(), mode]),
                _note("Create or edit a custom prompt in Writing → Modes, then select it here."),
                _card([self._shortcut_row("dictate_hotkey"), _divider(), mic]),
                _note("Hold the shortcut and speak. Release to transcribe and type at the cursor, tidied with the "
                      "dictation mode when that is on. A short tap does nothing. The audio goes to the first ready "
                      "source and is not saved.")]

    def _polish_changed(self, on):
        self._set("dictate_polish", on)
        self.dictate_popup.setEnabled_(on)

    def _writer_row(self, name, stt):
        """(switch, dot, status, fix, model, effort, (up, down), row) for one writer or speech-to-text source."""
        from .settings_ui import WIDTH, _spacer
        key = "stt_sources" if stt else "writer_sources"
        check = ui.switch(False, lambda on: self._set_order(key, ws.toggle(self.cfg[key], name, on)))
        check.setAccessibilityLabel_(f"Use {name}")
        dot, status = ui.label("●", 9), ui.label("", 11, secondary=True)
        fix = ui.button("", lambda: self.app.fix_writer(name, stt))
        fix.setControlSize_(AppKit.NSControlSizeSmall)
        model = effort = None
        if not stt:
            model = _popup(lambda i: self._model_picked(name, i))
            effort = _popup(lambda i: self._model_picked(name, None, i))
        arrows = (ui.symbol_button("chevron.up", f"Move {name} up",
                                   lambda: self._set_order(key, ws.move(self.cfg[key], name, -1)), 11),
                  ui.symbol_button("chevron.down", f"Move {name} down",
                                   lambda: self._set_order(key, ws.move(self.cfg[key], name, 1)), 11))
        text = ui.stack([ui.label(name, 13, bold=True), ui.stack([dot, status], vertical=False, spacing=4)],
                        spacing=1)
        row = ui.stack([ui.image_view(ui.source_icon(name, 28), 28), text, _spacer(), fix,
                        *[p for p in (model, effort) if p], *arrows, check], vertical=False, spacing=8)
        row.widthAnchor().constraintEqualToConstant_(WIDTH).setActive_(True)
        return check, dot, status, fix, model, effort, arrows, row

    def refresh_writing(self):
        """Rows in the saved order with the statuses and models the app last checked."""
        for key, rows, stack, statuses in (
                ("writer_sources", self.writer_rows, self.writer_list, self.app.writer_statuses),
                ("stt_sources", self.stt_rows, self.stt_list, self.app.stt_statuses)):
            order = self.cfg[key]
            for view in list(stack.arrangedSubviews()):
                stack.removeArrangedSubview_(view)
                view.removeFromSuperview()
            for i, (name, enabled) in enumerate(order):
                check, dot, status, fix, model, effort, (up, down), row = rows[name]
                state = statuses.get(name)
                check.setState_(1 if enabled else 0)
                text = LOCAL_TEXT.get(state) if name == "Local" else None
                status.setStringValue_(text or settings.status_text(state))
                dot.setTextColor_(getattr(AppKit.NSColor, {"ready": "systemGreenColor", None: "tertiaryLabelColor"}
                                          .get(state, "systemOrangeColor"))())
                title = self._fix_title(name, state)
                fix.setHidden_(not title)
                fix.setTitle_(title)
                if model:
                    self._fill_models(name, model, effort)
                up.setEnabled_(i > 0)
                down.setEnabled_(i < len(order) - 1)
                stack.addArrangedSubview_(row)
        self.mic_status.setStringValue_(MIC_TEXT[self.app.host.mic_status()])
        self._refresh_modes()
        if hasattr(self, "win"):
            self._fit()

    def _fix_title(self, name, state):
        from ..sources import fix
        if name == "Local":
            return "Download…" if state == "missing" else ""
        found = fix(name, state)
        return found[0] if found else ""

    def _fill_models(self, name, model, effort):
        models = self.app.writer_models
        chosen, chosen_effort = ws.chosen_model(self.cfg["writer_models"], name)
        choices = ws.model_choices(name, models)
        ids = [i for i, _ in choices]
        _fill(model, [label for _, label in choices], ids.index(chosen) if chosen in ids else 0)
        model.setEnabled_(len(choices) > 1)
        efforts = ws.effort_choices(name, models, chosen)
        _fill(effort, ["Default effort", *efforts], efforts.index(chosen_effort) + 1 if chosen_effort in efforts else 0)
        effort.setHidden_(not efforts)

    def _model_picked(self, name, model_index=None, effort_index=None):
        models = self.app.writer_models
        model, effort = ws.chosen_model(self.cfg["writer_models"], name)
        if model_index is not None:
            model = ws.model_choices(name, models)[model_index][0]
        if effort_index is not None:
            efforts = ws.effort_choices(name, models, model)
            effort = efforts[effort_index - 1] if effort_index else ""
        self._set("writer_models", ws.set_model(self.cfg["writer_models"], name, model, effort, models))
        self._fill_models(name, *self.writer_rows[name][4:6])

    def _set_order(self, key, order):
        self._set(key, order)
        self.refresh_writing()

    def _mode_list(self):
        return writer.all_modes(self.cfg["modes"])

    def _refresh_modes(self, reload=False):
        """Menus follow the saved modes. The editor keeps an unsaved draft unless its mode changed or `reload`."""
        names = [m.name for m in self._mode_list()]
        default = ws.default_mode(self.cfg["default_mode"], self.cfg["modes"])
        _fill(self.default_popup, names, names.index(default))
        dictate = ws.default_mode(self.cfg["dictate_mode"], self.cfg["modes"], "Clean dictation")
        _fill(self.dictate_popup, names, names.index(dictate))
        self.dictate_popup.setEnabled_(self.cfg["dictate_polish"])
        if self._editing not in names:
            self._editing = default
        _fill(self.mode_popup, names, names.index(self._editing))
        if reload or self._editing != self._loaded:
            self._edit_mode(self._editing)

    def _edit_mode(self, name):
        self._editing = self._loaded = name
        mode = writer.find_mode(name, self._mode_list())
        builtin = name in {m.name for m in writer.BUILTIN_MODES}
        self.mode_name.setStringValue_(mode.name)
        self.mode_name.setEditable_(not builtin)
        self.mode_text.setStringValue_(mode.instructions)
        overridden = any(m.get("name") == name for m in self.cfg["modes"] if isinstance(m, dict))
        self.mode_delete.setTitle_("Reset" if builtin else "Delete")
        self.mode_delete.setEnabled_(overridden or not builtin)
        self.mode_status.setStringValue_("Built-in modes keep their name." if builtin else "")

    def _default_picked(self, index):
        self._set("default_mode", self._mode_list()[index].name)

    def _set_modes(self, modes, editing):
        self._set("modes", modes)
        default = ws.default_mode(self.cfg["default_mode"], modes)
        if default != self.cfg["default_mode"]:
            self._set("default_mode", default)
        dictate = ws.default_mode(self.cfg["dictate_mode"], modes, "Clean dictation")
        if dictate != self.cfg["dictate_mode"]:
            self._set("dictate_mode", dictate)
        self._editing = editing
        self._refresh_modes(reload=True)

    def _add_mode(self):
        self._set_modes(*ws.add_mode(self.cfg["modes"]))

    def _save_mode(self):
        old = self._editing
        modes, name, error = ws.save_mode(self.cfg["modes"], old, self.mode_name.stringValue(),
                                          self.mode_text.stringValue())
        if error:
            self.mode_status.setStringValue_(error)
            return
        if self.cfg["default_mode"] == old:
            self._set("default_mode", name)
        if self.cfg["dictate_mode"] == old:
            self._set("dictate_mode", name)
        self._set_modes(modes, name)
        self.mode_status.setStringValue_("Saved.")

    def _delete_mode(self):
        self._set_modes(ws.delete_mode(self.cfg["modes"], self._editing), self._editing)
