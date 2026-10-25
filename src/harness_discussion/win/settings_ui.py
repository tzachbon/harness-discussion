"""The Settings window: native ttk controls laid out like Windows 11 Settings, a sidebar of pages. Changes apply at once."""
import sys
import tkinter as tk
import tkinter.font as tkfont
from tkinter import messagebox, ttk

from PIL import ImageTk

from .. import icons, settings, updates
from ..sources import fix as source_fix
from ..stt_server import connection as stt_connection
from .popup import fonts, work_area
from .sources_ui import CARD, SourcesSection
from .shortcut_row import ShortcutRow
from .about_page import AboutSection
from .summarize_page import SummarizePage
from .writing_page import WritingPage
from .stt_page import build_page as stt_page

PAGE, BORDER, SUBTLE, SELECTED = "#f3f3f3", "#e5e5e5", "#5f5f5f", "#e6e6e6"
PAGES = ("Voices", "Captions", "Reading", "Summarize", "Writing", "Dictation", "Speech to Text", "General")


class SettingsWindow(SourcesSection, SummarizePage, ShortcutRow, WritingPage, AboutSection):
    def __init__(self, root, cfg, icon, on_change, on_test, stt_state=lambda: None,
                 update_state=lambda: ("Ready to check.", False), on_update=lambda: None, engines=(),
                 statuses=dict, on_play=lambda name: None, welcome=False, on_fix=lambda name: None, summarizer=None,
                 on_record=lambda recording: None, writer_catalogs=dict, writer_statuses=dict,
                 on_writer_fix=lambda name, stt=False: None, dictate_status=lambda: "Not checked",
                 stt_statuses=dict, on_mic_settings=lambda: None):
        self.cfg, self.on_change, self.on_test, self._save_job, self._pending_key = cfg, on_change, on_test, None, None
        self.statuses, self.on_play, self._engines = statuses, on_play, {e.name: e for e in engines}
        self.on_fix, self.on_record = on_fix, on_record
        self.writer_catalogs, self.writer_statuses, self.on_writer_fix = writer_catalogs, writer_statuses, on_writer_fix
        self.dictate_status = dictate_status
        self.stt_statuses, self.on_mic_settings = stt_statuses, on_mic_settings
        self.stt_state = stt_state  # None when running, else why not
        self.update_state = update_state
        w = self.win = tk.Toplevel(root, bg=PAGE)
        w.title("Harness Discussion settings")
        w.resizable(False, False)
        self._icon = ImageTk.PhotoImage(icon, master=w)
        w.iconphoto(False, self._icon)
        _, text = fonts(root)
        st = ttk.Style(w)
        st.theme_use("vista")
        for name in ("TLabel", "TCheckbutton", "Horizontal.TScale"):
            st.configure(f"Card.{name}", background=CARD, font=(text, 10))
        st.configure("Sub.Card.TLabel", foreground=SUBTLE, font=(text, 9))
        st.configure("TButton", font=(text, 10), padding=(12, 4))

        sidebar = tk.Frame(w, bg=PAGE, padx=10, pady=16)
        sidebar.pack(side="left", fill="y")
        self._app_icon = ImageTk.PhotoImage(icon.resize((28, 28)), master=w)
        tk.Label(sidebar, image=self._app_icon, text="  Harness Discussion", compound="left", bg=PAGE,
                 font=(text, 11, "bold"), anchor="w").pack(fill="x", padx=6, pady=(0, 14))
        self._tiles = {name: (ImageTk.PhotoImage(icons.page_image(name, 20), master=w),
                              ImageTk.PhotoImage(icons.badged(icons.page_image(name, 20)), master=w)) for name in PAGES}
        self.nav = [tk.Button(sidebar, text="   " + name, image=self._tiles[name][0], compound="left", anchor="w",
                              relief="flat", bd=0, bg=PAGE, activebackground=SELECTED, font=(text, 10),
                              width=190, padx=10, pady=7, cursor="hand2", command=lambda i=i: self.show_page(i))
                    for i, name in enumerate(PAGES)]
        for button in self.nav:
            button.pack(fill="x", pady=1)
        self._page = 0
        main = tk.Frame(w, bg=PAGE, padx=24, pady=20)
        main.pack(side="left", fill="both", expand=True)

        self.footer = tk.Frame(main, bg=PAGE)
        self.close_button = ttk.Button(self.footer, text="Close", command=self.close)
        self.close_button.pack(anchor="e", pady=(16, 0))
        self.footer.pack(side="bottom", fill="x")
        self._scroll_frame = tk.Frame(main, bg=PAGE)
        self._scroll_canvas = tk.Canvas(self._scroll_frame, bg=PAGE, bd=0, highlightthickness=0)
        self._scrollbar = ttk.Scrollbar(self._scroll_frame, orient="vertical",
                                        command=self._scroll_canvas.yview)
        self._scroll_canvas.configure(yscrollcommand=self._scrollbar.set)
        self.content = tk.Frame(self._scroll_canvas, bg=PAGE)
        self._content_window = self._scroll_canvas.create_window((0, 0), window=self.content, anchor="nw")
        self.content.bind("<Configure>", lambda _e: self._scroll_canvas.configure(
            scrollregion=self._scroll_canvas.bbox("all")))
        self._scroll_canvas.bind("<Configure>", lambda e: self._scroll_canvas.itemconfigure(
            self._content_window, width=e.width))
        self._scroll_canvas.pack(side="left", fill="both", expand=True)
        self._scroll_frame.pack(fill="both", expand=True)

        self.title = tk.Label(self.content, bg=PAGE, font=(text.replace("Text", "Display"), 20, "bold"), anchor="w")
        self.title.pack(fill="x", pady=(0, 8))
        self.pages = [tk.Frame(self.content, bg=PAGE) for _ in PAGES]
        voices, captions, reading, summary, writing, dictation, stt, general = self.pages

        card = self._card(voices, "Sources")
        self._sources_section(card, text, welcome)
        ttk.Label(voices, text="Sources read top to bottom. If one can't read, the next one does. Codex uses your "
                  "ChatGPT sign-in and can read slower but not faster, so above 1× the next source reads. Edge "
                  "sends the text to Microsoft. Windows voices stay on this PC.", background=PAGE,
                  foreground=SUBTLE, font=(text, 9), wraplength=470).pack(anchor="w", pady=(6, 0))
        card = self._card(voices, "Speed")
        row = tk.Frame(card, bg=CARD)
        row.pack(fill="x")
        self.speed = tk.DoubleVar(w, cfg["speed"])
        ttk.Scale(row, from_=settings.SPEEDS[0], to=settings.SPEEDS[1], variable=self.speed, length=260,
                  style="Card.Horizontal.TScale", command=self._speed_changed).pack(side="left")
        self.speed_lbl = ttk.Label(row, style="Card.TLabel", width=6, font=(text, 10, "bold"))
        self.speed_lbl.pack(side="left", padx=8)
        self._show_speed()
        ttk.Button(row, text="Test voice", command=self._test).pack(side="right")

        self.startup = tk.BooleanVar(w, settings.startup_enabled())
        card = self._card(general)
        row = tk.Frame(card, bg=CARD)
        row.pack(fill="x")
        self._about_icon = ImageTk.PhotoImage(icon.resize((40, 40)), master=w)
        tk.Label(row, image=self._about_icon, bg=CARD).pack(side="left", padx=(0, 12))
        about = tk.Frame(row, bg=CARD)
        about.pack(side="left")
        ttk.Label(about, text="Harness Discussion", style="Card.TLabel", font=(text, 11, "bold")).pack(anchor="w")
        ttk.Label(about, text=f"Version {updates.current_version()}", style="Sub.Card.TLabel").pack(anchor="w")
        self.update_button = ttk.Button(row, text="Check and install update", command=on_update)
        self.update_button.pack(side="right")
        self.update_status = ttk.Label(card, style="Sub.Card.TLabel", wraplength=440)
        self.update_status.pack(anchor="w", pady=(10, 0))
        self._about_section(general, text)
        card = self._card(general, "Startup and updates")
        ttk.Checkbutton(card, text="Start Harness Discussion when I sign in to Windows", style="Card.TCheckbutton",
                        variable=self.startup, command=self._startup_changed).pack(anchor="w")
        self.auto_update = tk.BooleanVar(w, cfg.get("auto_update", False))
        self.auto_update_button = ttk.Checkbutton(card, text="Install updates automatically", style="Card.TCheckbutton",
            variable=self.auto_update, command=lambda: self._set("auto_update", self.auto_update.get()))
        self.auto_update_button.pack(anchor="w", pady=(10, 0))

        card = self._card(captions, "Appearance")
        row = tk.Frame(card, bg=CARD)
        row.pack(fill="x")
        ttk.Label(row, text="Font size", style="Card.TLabel", width=12).pack(side="left")
        self.caption_font_value = tk.StringVar(w, str(cfg["caption_font_size"]))
        self.caption_font_size = ttk.Spinbox(row, from_=8, to=24, increment=1, width=5,
                                             textvariable=self.caption_font_value,
                                             command=self._caption_font_changed)
        self.caption_font_size.bind("<Return>", self._caption_font_changed)
        self.caption_font_size.bind("<FocusOut>", self._caption_font_changed)
        self.caption_font_size.pack(side="left", padx=8)
        self.caption_background = tk.BooleanVar(w, cfg["caption_background"])
        self.caption_background_check = ttk.Checkbutton(
            card, text="Show a background behind captions", style="Card.TCheckbutton",
            variable=self.caption_background, command=self._caption_background_changed)
        self.caption_background_check.pack(anchor="w", pady=(10, 0))
        row = tk.Frame(card, bg=CARD)
        row.pack(fill="x", pady=(8, 0))
        ttk.Label(row, text="Background opacity", style="Card.TLabel", width=17).pack(side="left")
        self.caption_opacity = tk.DoubleVar(w, round(cfg["caption_background_opacity"] * 100))
        self.caption_opacity_scale = ttk.Scale(row, from_=0, to=100, variable=self.caption_opacity,
                                               length=210, style="Card.Horizontal.TScale")
        self.caption_opacity_scale.pack(side="left", padx=8)
        self.caption_opacity_label = ttk.Label(row, style="Card.TLabel", width=5)
        self.caption_opacity_label.pack(side="left")
        self.caption_opacity.trace_add("write", self._caption_opacity_changed)
        self._caption_opacity_changed()
        self._caption_opacity_state()

        card = self._card(reading, "Selection")
        self.clip = tk.BooleanVar(w, False)
        self.clip_check = ttk.Checkbutton(card, text="Read apps that hide their selection (unavailable)",
                                         style="Card.TCheckbutton", variable=self.clip, state="disabled")
        self.clip_check.pack(anchor="w")
        self.clip_note = ttk.Label(card, text="Automatic copying is unavailable to protect your clipboard. "
                                  "Apps that hide selections cannot be read. List numbers may be omitted.",
                                  style="Sub.Card.TLabel", wraplength=440)
        self.clip_note.pack(anchor="w", pady=(4, 0))
        self.prefetch = tk.BooleanVar(w, cfg["prefetch"])
        ttk.Checkbutton(card, text="Prepare speech as soon as I select text", style="Card.TCheckbutton",
                        variable=self.prefetch,
                        command=lambda: self._set("prefetch", self.prefetch.get())).pack(anchor="w", pady=(12, 0))
        self._shortcut_row(card)
        ttk.Label(card, text="Sends the opening sentences to Cursor or Microsoft before you press play. "
                  "Applies when Cursor or Edge reads first.", style="Sub.Card.TLabel",
                  wraplength=420).pack(anchor="w", pady=(4, 0))

        self._summarize_page(summary, text, summarizer)
        self._writing_page(writing, text)
        self._dictation_page(dictation)

        stt_page(self, stt)

        w.protocol("WM_DELETE_WINDOW", self.close)
        self._fit_work_area(root)
        self.show_page(0)
        self.focus()

    def show_page(self, index):
        self._page = index
        for i, page in enumerate(self.pages):
            page.pack_forget()
        self.pages[index].pack(fill="x")
        self.title["text"] = PAGES[index]
        self._scroll_canvas.yview_moveto(0)
        self._refresh_nav()

    def _refresh_nav(self):
        """The selected page is shaded and bold; Voices carries a dot while an enabled source needs a fix."""
        if not hasattr(self, "nav"):
            return
        statuses = self.statuses()
        attention = any(on and source_fix(name, statuses.get(name)) for name, on in self.cfg["sources"])
        for i, (button, name) in enumerate(zip(self.nav, PAGES)):
            selected = i == self._page
            button.configure(bg=SELECTED if selected else PAGE, image=self._tiles[name][attention and i == 0],
                             font=(fonts(self.win)[1], 10, "bold" if selected else "normal"))

    def _card(self, page, title=None):
        if title:
            tk.Label(page, text=title, bg=PAGE, anchor="w",
                     font=tkfont.Font(family=fonts(self.win)[1], size=10, weight="bold")
                     ).pack(fill="x", pady=(12, 4))
        card = tk.Frame(page, bg=CARD, padx=16, pady=14, highlightthickness=1,
                        highlightbackground=BORDER, highlightcolor=BORDER)
        card.pack(fill="x", pady=(0 if title else 4, 0))
        return card

    def _fit_work_area(self, root):
        """Sized for the tallest page, so switching pages never resizes the window; scrolls on a short screen."""
        width = content_height = 0
        for i in range(len(self.pages)):
            self.show_page(i)
            self.win.update_idletasks()
            width = max(width, self.content.winfo_reqwidth())
            content_height = max(content_height, self.content.winfo_reqheight())
        self._scroll_canvas.configure(width=width, height=content_height)
        self.win.update_idletasks()
        area = work_area(root.winfo_pointerx(), root.winfo_pointery())
        area_width, area_height = area.right - area.left, area.bottom - area.top
        requested_height = self.win.winfo_reqheight()
        max_height = max(1, area_height - 48)  # room for the native title bar and borders
        visible_content_height = max(1, content_height - max(0, requested_height - max_height))
        if visible_content_height < content_height:
            self._scroll_canvas.configure(height=visible_content_height)
            self._scrollbar.pack(side="right", fill="y")
        self.win.update_idletasks()
        width, height = self.win.winfo_reqwidth(), self.win.winfo_reqheight()
        x = area.left + max(0, (area_width - width) // 2)
        y = area.top + max(0, (area_height - height) // 2)
        self.win.geometry(f"{width}x{height}+{x}+{y}")

    @property
    def alive(self):
        return bool(self.win.winfo_exists())

    def focus(self):
        self._refresh_stt()
        self.refresh_updates()
        self.win.deiconify()
        self.win.lift()
        self.win.focus_force()

    def refresh_updates(self):
        text, busy = self.update_state()
        installed = getattr(sys, "frozen", False)
        self.update_button["state"] = "normal" if installed and not busy else "disabled"
        self.auto_update_button["state"] = "normal" if installed else "disabled"
        self.update_status["text"] = text if installed else "Updates are available in installed builds."

    def _refresh_stt(self):
        try:
            values = stt_connection()
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            values = ("", "")
        for variable, value in zip(self.stt_values.values(), values):
            variable.set(value)
        for button in self.stt_copy_buttons:
            button["state"] = "normal" if values[0] else "disabled"
        problem = self.stt_state()
        self.stt_status["text"] = (problem if problem else "Running." if values[0]
                                   else "Turn on the server above.")

    def _stt_toggled(self):
        self._set("stt_server", self.stt_on.get())
        self.win.after(300, self._refresh_stt)

    def _copy_stt(self, name):
        self._refresh_stt()
        value = self.stt_values[name].get()
        if value:
            self.win.clipboard_clear()
            self.win.clipboard_append(value)
            self.stt_status["text"] = f"{name} copied."

    def close(self):
        self._flush_pending()
        self._stop_recording()
        self.on_record(False)
        self.win.destroy()

    def _set(self, key, value):
        self.cfg[key] = value
        self.on_change(key, value)

    def _startup_changed(self):
        try:
            settings.set_startup(self.startup.get())
        except OSError as e:
            self.startup.set(settings.startup_enabled())
            messagebox.showerror("Harness Discussion", f"Could not change sign-in startup: {e}", parent=self.win)

    def _show_speed(self):
        self.speed_lbl["text"] = settings.speed_label(self.speed.get())

    def set_speed(self, speed):
        """Mirrors a speed picked in the popup, without saving it again."""
        self.speed.set(speed)
        self.cfg["speed"] = speed
        self._show_speed()

    def _speed_changed(self, _value):
        self.speed.set(round(self.speed.get() * 20) / 20)  # 0.05 steps
        self._show_speed()
        self.cfg["speed"] = self.speed.get()
        self.on_change("speed", self.cfg["speed"], False)  # the next read uses it at once
        self._schedule_save("speed")

    def _schedule_save(self, key):
        if self._save_job:
            self.win.after_cancel(self._save_job)
        self._pending_key = key
        self._save_job = self.win.after(400, self._save_pending)

    def _save_pending(self):
        self._save_job = None
        key, self._pending_key = self._pending_key, None
        if key is not None:
            self._set(key, self.cfg[key])

    def _flush_pending(self):
        self._about_changed()
        self._caption_font_changed()
        if self._save_job:
            self.win.after_cancel(self._save_job)
            self._save_pending()

    def _set_debounced(self, key, value):
        if self.cfg[key] == value:
            return
        self.cfg[key] = value
        self.on_change(key, value, False)
        self._schedule_save(key)

    def _caption_font_changed(self, _event=None):
        try:
            value = int(self.caption_font_value.get())
        except ValueError:
            self.caption_font_value.set(str(self.cfg["caption_font_size"]))
            return "break"
        value = min(max(value, 8), 24)
        self.caption_font_value.set(str(value))
        self._set_debounced("caption_font_size", value)
        return "break"

    def _caption_background_changed(self):
        self._set_debounced("caption_background", self.caption_background.get())
        self._caption_opacity_state()

    def _caption_opacity_changed(self, *_):
        percent = min(max(round(self.caption_opacity.get()), 0), 100)
        if self.caption_opacity.get() != percent:
            self.caption_opacity.set(percent)
        self.caption_opacity_label["text"] = f"{percent}%"
        self._set_debounced("caption_background_opacity", percent / 100)

    def _caption_opacity_state(self):
        self.caption_opacity_scale.configure(
            state="normal" if self.caption_background.get() else "disabled")

    def _test(self):
        self._flush_pending()
        self.on_test(f"This is how Harness Discussion sounds at {self.speed_lbl['text']} speed.")
