"""Windows meeting settings, using the app's consent and calendar paths."""
import os
import tkinter as tk
from tkinter import ttk

from .. import meeting_copy as copy, meetings
from .sources_ui import CARD


class MeetingsPage:
    def _meetings_page(self, page, font, app):
        from .settings_ui import PAGE, SUBTLE
        self.meeting_app = app
        card = self._card(page)
        self.meetings_on = tk.BooleanVar(self.win, self.cfg.get("meetings", False))
        self.meetings_check = ttk.Checkbutton(card, text=copy.MEETING_NOTES, style="Card.TCheckbutton",
                                               variable=self.meetings_on, command=self._meetings_toggled)
        self.meetings_check.pack(anchor="w")
        ttk.Label(card, text=copy.RECORD_PC, style="Sub.Card.TLabel").pack(anchor="w", padx=(24, 0))
        self.meeting_ask = tk.BooleanVar(self.win, self.cfg.get("meeting_ask", True))
        ttk.Checkbutton(card, text=copy.ASK_WHEN_CALL, style="Card.TCheckbutton", variable=self.meeting_ask,
                        command=lambda: self._set("meeting_ask", self.meeting_ask.get())).pack(anchor="w", pady=(10, 0))
        ttk.Label(card, text=copy.ASK_HELP, style="Sub.Card.TLabel", wraplength=420).pack(anchor="w", padx=(24, 0))

        card = self._card(page, copy.CALENDAR)
        ttk.Label(card, text=copy.CALENDAR_LINK, style="Card.TLabel").pack(anchor="w")
        row = tk.Frame(card, bg=CARD)
        row.pack(fill="x", pady=(4, 0))
        self.meeting_url = tk.StringVar(self.win, self.cfg.get("meeting_ics_url", ""))
        self.meeting_url_entry = ttk.Entry(row, textvariable=self.meeting_url, show="•")
        self.meeting_url_entry.pack(side="left", fill="x", expand=True)
        self.meeting_url_entry.bind("<FocusOut>", self._meeting_url_changed)
        self.meeting_url_entry.bind("<Return>", self._meeting_url_changed)
        self.meeting_show = tk.BooleanVar(self.win, False)
        ttk.Checkbutton(row, text=copy.SHOW, variable=self.meeting_show, style="Card.TCheckbutton",
                        command=lambda: self.meeting_url_entry.configure(show="" if self.meeting_show.get() else "•")
                        ).pack(side="left", padx=6)
        self.meeting_test = ttk.Button(row, text=copy.TEST, command=self._test_meeting_calendar)
        self.meeting_test.pack(side="left")
        self.meeting_calendar_status = ttk.Label(card, style="Sub.Card.TLabel")
        self.meeting_calendar_status.pack(anchor="w", pady=(6, 0))
        ttk.Label(card, text=copy.CALENDAR_HELP, style="Sub.Card.TLabel", wraplength=440).pack(anchor="w", pady=(6, 0))
        card = self._card(page, copy.STORAGE)
        row = tk.Frame(card, bg=CARD)
        row.pack(fill="x")
        self.meeting_storage = ttk.Label(row, style="Card.TLabel")
        self.meeting_storage.pack(side="left", fill="x", expand=True)
        buttons = tk.Frame(row, bg=CARD)
        buttons.pack(side="right")
        ttk.Button(buttons, text=copy.MEETING_NOTES_MENU,
                   command=lambda: self.meeting_app._meeting_open()).pack(fill="x")
        ttk.Button(buttons, text=copy.OPEN_FOLDER, command=self._open_meetings_folder).pack(fill="x", pady=(5, 0))
        ttk.Label(page, text=copy.SETTINGS_NOTE, background=PAGE, foreground=SUBTLE,
                  font=(font, 9), wraplength=470).pack(anchor="w", pady=(6, 0))
        self.win.after(500, self._poll_meetings)

    def _meetings_toggled(self):
        enabled = self.meetings_on.get()
        if self.meeting_app and hasattr(self.meeting_app, "set_meetings"):
            accepted = self.meeting_app.set_meetings(enabled)
            if accepted:
                self.cfg["meetings"] = enabled
            else:
                self.meetings_on.set(self.cfg.get("meetings", False))
        else:
            self.meetings_on.set(self.cfg.get("meetings", False))

    def _meeting_url_changed(self, _event=None):
        value = self.meeting_url.get()
        if value != self.cfg.get("meeting_ics_url", ""):
            self._set("meeting_ics_url", value)

    def _test_meeting_calendar(self):
        self._meeting_url_changed()
        if self.meeting_app and hasattr(self.meeting_app, "refresh_meeting_calendar"):
            self.meeting_app.calendar_status = copy.CHECKING
            self.meeting_calendar_status.configure(text=copy.CHECKING)
            self.meeting_app.refresh_meeting_calendar()

    def _poll_meetings(self):
        self._refresh_calendar_status()
        self.win.after(500, self._poll_meetings)

    def refresh_meetings(self):
        self._refresh_calendar_status()
        self.meeting_storage.configure(text=copy.STORAGE_SIZE.format(n=len(meetings.all()),
                                       size=f"{meetings.size_bytes() // (1024 * 1024)} MB"))

    def _refresh_calendar_status(self):
        status = getattr(self.meeting_app, "calendar_status", copy.CALENDAR_NOT_SET)
        color = "#b42318" if status in (copy.CALENDAR_ERROR, copy.CALENDAR_INVALID) else (
            "#15803d" if status.startswith("●") else "#5f5f5f")
        self.meeting_calendar_status.configure(text=status, foreground=color)

    def _open_meetings_folder(self):
        meetings.DIR.mkdir(parents=True, exist_ok=True)
        os.startfile(meetings.DIR)
