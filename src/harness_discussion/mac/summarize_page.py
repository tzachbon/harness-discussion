"""The Summarize page of Settings on macOS: the local model's status, with Download, Cancel or Remove."""
import AppKit

from .. import summarize
from . import ui

# Summarize state -> (status, dot color, button title or None)
SUMMARY_STATES = {"missing": ("Not downloaded", "tertiaryLabelColor", "Download"),
                  "downloading": ("Downloading {percent}% of {size}", "systemBlueColor", "Cancel"),
                  "ready": ("Ready", "systemGreenColor", "Remove"),
                  "unsupported": ("Not available on this Mac", "tertiaryLabelColor", None)}


class SummarizePage:
    """Mixed into SettingsWindow, which owns app and _fit."""

    def _summarize_page(self):
        from .settings_ui import WIDTH, _card, _note, _spacer  # settings_ui imports this module
        self.summary_dot, self.summary_status = ui.label("●", 9), ui.label("", 11, secondary=True)
        self.summary_bar = AppKit.NSProgressIndicator.alloc().init()
        self.summary_bar.setIndeterminate_(False)
        self.summary_bar.setMaxValue_(1.0)
        self.summary_bar.widthAnchor().constraintEqualToConstant_(WIDTH - 130).setActive_(True)
        self.summary_button = ui.button("Download", self.app.summarize_action)
        model = ui.stack([ui.label(summarize.MODEL_NAME, 13, bold=True),
                          ui.label(f"{summarize.SIZE_LABEL} · runs on this Mac", 11, secondary=True)],
                         vertical=False, spacing=6)
        text = ui.stack([model, ui.stack([self.summary_dot, self.summary_status], vertical=False, spacing=4),
                         self.summary_bar], spacing=3)
        text.setAlignment_(AppKit.NSLayoutAttributeLeading)
        row = ui.stack([text, _spacer(), self.summary_button], vertical=False)
        row.widthAnchor().constraintEqualToConstant_(WIDTH).setActive_(True)
        return [_card([row]),
                _note("The ✦ button next to Play summarizes the selection on this Mac, then reads the summary "
                      "with your voice sources. The text stays on this Mac. The model stays loaded for 5 "
                      f"minutes after a summary. Remove frees the {summarize.SIZE_LABEL}.")]

    def refresh_summarize(self):
        state, progress = self.app.summarize_state()
        text, color, action = SUMMARY_STATES[state if progress is None else "downloading"]
        if progress is not None:
            text = text.format(percent=int(progress * 100), size=summarize.download_size_label())
        self.summary_status.setStringValue_(text)
        self.summary_dot.setTextColor_(getattr(AppKit.NSColor, color)())
        self.summary_bar.setHidden_(progress is None)
        self.summary_bar.setDoubleValue_(progress or 0)
        self.summary_button.setHidden_(action is None)
        self.summary_button.setTitle_(action or "")
        if hasattr(self, "win"):  # the progress bar makes the row taller
            self._fit()
