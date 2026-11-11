"""Windows meeting list and selectable notes, refreshed while processing continues."""
import tkinter as tk
from datetime import datetime
from tkinter import ttk

from .. import meeting_copy as copy, meetings
from .toast import Toast

PAGE, CARD, SUBTLE = "#f3f3f3", "white", "#5f5f5f"


def _date(value):
    return datetime.fromisoformat(value).astimezone()


def _length(meeting):
    if not meeting.ended_at:
        return ""
    minutes = max(0, int((_date(meeting.ended_at) - _date(meeting.started_at)).total_seconds() // 60))
    return f"{minutes} min"


def _state(meeting, app):
    return {
        "recording": copy.STATE_RECORDING,
        "transcribing": copy.STATE_TRANSCRIBING.format(done=app.meeting_progress(meeting.id)[0],
                                                        total=app.meeting_progress(meeting.id)[1]),
        "noting": copy.STATE_MAKING,
        "interrupted": copy.STATE_INTERRUPTED,
        "failed_transcript": copy.STATE_TRANSCRIPTION_FAILED.format(reason=meeting.error),
        "failed_notes": copy.STATE_NOTES_FAILED.format(reason=meeting.error),
        "done": copy.STATE_PARTIAL if meeting.error else "",
    }.get(meeting.state, "")


class NotesWindow:
    def __init__(self, root, app, meeting_id=None):
        self.app, self.host = app, app.host
        self.current = None
        self._snapshot = None
        self._copy_toast = None
        self.win = tk.Toplevel(root, bg=PAGE)
        self.win.title(copy.WINDOW_TITLE)
        self.win.geometry("1000x650")
        self.win.minsize(720, 450)
        self.win.bind("<Escape>", lambda e: self.win.destroy())
        self.win.bind("<Delete>", lambda e: self.delete() if self.win.focus_get() == self.list else None)
        pane = tk.Frame(self.win, bg=PAGE, padx=16, pady=16)
        pane.pack(fill="both", expand=True)
        style = ttk.Style(self.win)
        style.configure("Meeting.Treeview", rowheight=54, font=("Segoe UI", 10))
        self.list = ttk.Treeview(pane, show="tree", selectmode="browse", style="Meeting.Treeview")
        self.list.column("#0", width=270, stretch=False)
        self.list.pack(side="left", fill="y", padx=(0, 12))
        self.list.bind("<<TreeviewSelect>>", self._selected)
        detail = tk.Frame(pane, bg=CARD, padx=18, pady=16)
        detail.pack(side="left", fill="both", expand=True)
        self.title = tk.Label(detail, bg=CARD, anchor="w", font=("Segoe UI", 16, "bold"))
        self.title.pack(fill="x")
        self.event = tk.Label(detail, bg=CARD, fg=SUBTLE, anchor="w", font=("Segoe UI", 10))
        self.meta = tk.Label(detail, bg=CARD, fg=SUBTLE, anchor="w", font=("Segoe UI", 10))
        self.meta.pack(fill="x", pady=(4, 0))
        self.state_row = state_row = tk.Frame(detail, bg=CARD)
        self.status = tk.Label(state_row, bg=CARD, fg=SUBTLE, anchor="w", font=("Segoe UI", 10))
        self.status.pack(side="left")
        self.retry = ttk.Button(state_row)
        self.tabs = ttk.Notebook(detail)
        self.tabs.pack(fill="both", expand=True, pady=(12, 10))
        self.notes = self._text(copy.NOTES)
        self.transcript = self._text(copy.TRANSCRIPT)
        self.notes.tag_configure("heading", font=("Segoe UI", 11, "bold"))
        self.transcript.tag_configure("You", foreground="#0067c0")
        self.transcript.tag_configure("Them", foreground=SUBTLE)
        row = tk.Frame(detail, bg=CARD)
        row.pack(side="bottom", fill="x", before=self.tabs)
        self.copy_button = ttk.Button(row, text=copy.COPY_NOTES, command=self.copy_notes)
        self.copy_button.pack(side="left", padx=(0, 6))
        self.export_button = ttk.Button(row, text=copy.EXPORT_MARKDOWN, command=self.export)
        self.export_button.pack(side="left", padx=(0, 6))
        self.folder_button = ttk.Button(row, text=copy.OPEN_FOLDER, command=self.folder)
        self.folder_button.pack(side="left")
        self.delete_button = ttk.Button(row, text=copy.DELETE, command=self.delete)
        self.delete_button.pack(side="right")
        self.delete_button.bind("<Enter>", self._delete_hint)
        self.delete_button.bind("<Leave>", self._hide_hint)
        self.hint = None
        self.empty = tk.Label(detail, text=copy.EMPTY_LIST, bg=CARD, fg=SUBTLE, wraplength=460,
                              font=("Segoe UI", 10))
        self.open(meeting_id)
        self._tick()

    def _delete_hint(self, _event):
        if self.current and self.delete_button.instate(["disabled"]) and not self.hint:
            self.hint = tk.Toplevel(self.win)
            self.hint.overrideredirect(True)
            self.hint.geometry(f"+{self.delete_button.winfo_rootx()}+{self.delete_button.winfo_rooty() + self.delete_button.winfo_height()}")
            tk.Label(self.hint, text=copy.STOP_FIRST, bg="#ffffe1", relief="solid", borderwidth=1).pack()

    def _hide_hint(self, _event=None):
        if self.hint:
            self.hint.destroy()
            self.hint = None

    def _text(self, name):
        frame = ttk.Frame(self.tabs)
        self.tabs.add(frame, text=name)
        text = tk.Text(frame, wrap="word", relief="flat", padx=12, pady=12, font=("Segoe UI", 10),
                       state="disabled", undo=False)
        scroll = ttk.Scrollbar(frame, command=text.yview)
        text.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        text.pack(fill="both", expand=True)
        text.bind("<Control-a>", lambda e: (text.tag_add("sel", "1.0", "end-1c"), "break")[1])
        return text

    def open(self, meeting_id=None):
        self.win.deiconify()
        self.win.lift()
        self.refresh(meeting_id or self.current)

    def _tick(self):
        self.refresh(self.current)
        self.win.after(1000, self._tick)

    def refresh(self, meeting_id=None):
        items = meetings.all()
        snapshot = [(m.id, m.title, m.started_at, m.ended_at, m.state, m.error,
                     self.app.meeting_progress(m.id), self.app.meeting_busy(m.id),
                     meetings.notes(m.id), len(meetings.parts(m.id))) for m in items]
        if snapshot == self._snapshot and (meeting_id is None or meeting_id == self.current):
            return
        self._snapshot = snapshot
        self.list.delete(*self.list.get_children())
        for m in items:
            state = _state(m, self.app)
            subtitle = " · ".join(filter(None, (_date(m.started_at).strftime("%a %d %b %H:%M"), _length(m))))
            self.list.insert("", "end", iid=m.id, text=f"{m.title}\n{subtitle}" + (f" · {state}" if state else ""))
        ids = [m.id for m in items]
        self.current = meeting_id if meeting_id in ids else (ids[0] if ids else None)
        if self.current:
            self.list.selection_set(self.current)
            self.list.focus(self.current)
            self.empty.place_forget()
            self._detail(next(m for m in items if m.id == self.current))
        else:
            self._detail(None)
            self.empty.place(relx=.5, rely=.5, anchor="center")

    def _selected(self, _event=None):
        selected = self.list.selection()
        if selected and selected[0] != self.current:
            self.current = selected[0]
            self._detail(meetings.load(self.current))

    def _detail(self, m):
        self.title.configure(text=m.title if m else "")
        self.event.configure(text="")
        self.event.pack_forget()
        self.meta.configure(text="")
        self.status.configure(text=_state(m, self.app) if m else "")
        self.retry.pack_forget()
        if m:
            if m.event:
                start, end = _date(m.event['start']), _date(m.event['end'])
                self.event.configure(text=f"📅 {m.event['title']} · {start:%a %d %b %Y, %H:%M}–{end:%H:%M}")
                self.event.pack(fill="x", pady=(4, 0), after=self.title)
            start = _date(m.started_at)
            times = start.strftime("%H:%M") + ("–" + _date(m.ended_at).strftime("%H:%M") if m.ended_at else "")
            self.meta.configure(text=" · ".join(filter(None, (m.app, times, _length(m)))))
            if m.state in ("failed_transcript", "failed_notes") or (m.state == "done" and m.error):
                transcript = m.state != "failed_notes"
                self.retry.configure(text=copy.TRANSCRIBE_AGAIN if transcript else copy.MAKE_NOTES_AGAIN,
                                     command=lambda: self._retry(transcript))
                self.retry.pack(side="left", padx=8)
        if self.status.cget("text") or self.retry.winfo_manager():
            self.state_row.pack(fill="x", pady=(6, 0), before=self.tabs)
        else:
            self.state_row.pack_forget()
        for button in (self.copy_button, self.export_button, self.folder_button):
            button.state(["!disabled"] if m else ["disabled"])
        busy = bool(m and (self.app.meeting_busy(m.id) or m.state not in ("done", "failed_transcript", "failed_notes")))
        self.delete_button.state(["!disabled"] if m and not busy else ["disabled"])
        self._fill(self.notes, meetings.notes(m.id) or copy.NOTES_PLACEHOLDER if m else "", True)
        self.transcript.configure(state="normal")
        self.transcript.delete("1.0", "end")
        if m:
            for seconds, speaker, text in meetings.lines(meetings.parts(m.id)):
                self.transcript.insert("end", f"{int(seconds)//60:02d}:{int(seconds)%60:02d}  ")
                self.transcript.insert("end", speaker, speaker)
                self.transcript.insert("end", f"  {text}\n")
        self.transcript.configure(state="disabled")

    def _fill(self, widget, text, headings=False):
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        for line in text.splitlines(keepends=True):
            heading = headings and line.startswith("#") and line.lstrip("#").startswith(" ")
            widget.insert("end", line.lstrip("#").lstrip(" ") if heading else line, "heading" if heading else ())
        widget.configure(state="disabled")

    def _retry(self, transcript):
        if self.current:
            (self.app.redo_transcript if transcript else self.app.redo_notes)(self.current)
            self._snapshot = None
            self.refresh(self.current)

    def _markdown(self, transcript):
        m = meetings.load(self.current)
        return meetings.markdown(m, meetings.notes(m.id), meetings.lines(meetings.parts(m.id)), transcript=transcript)

    def copy_notes(self):
        if self.current and self.host.copy_text(self._markdown(False)):
            if not self._copy_toast:
                self._copy_toast = Toast(self.win)
            self._copy_toast.show(copy.NOTES_COPIED, self.copy_button.winfo_rootx(),
                                  self.copy_button.winfo_rooty(), hide_s=3)

    def export(self):
        if self.current:
            m = meetings.load(self.current)
            self.host.save_text(copy.export_name(m), self._markdown(True))

    def folder(self):
        if self.current:
            self.host.reveal(meetings.load(self.current).folder)

    def delete(self):
        if not self.current or self.delete_button.instate(["disabled"]) or self.app.meeting_busy(self.current):
            return
        if self.host.confirm(copy.WINDOW_TITLE, copy.DELETE_CONFIRM, ok=copy.DELETE_OK):
            if self.app.delete_meeting(self.current):
                self._snapshot = None
                self.refresh()


