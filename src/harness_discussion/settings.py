"""User settings, plus start-at-sign-in through a Task Scheduler logon task on Windows."""
import json
import math
import os
import shutil
import sys
import tempfile

from . import hotkeys

if sys.platform == "win32":
    import winreg

if sys.platform == "darwin":
    APPDATA = LOCALAPPDATA = os.path.expanduser("~/Library/Application Support")
else:
    APPDATA = os.environ.get("APPDATA", os.path.expanduser("~"))
    LOCALAPPDATA = os.environ.get("LOCALAPPDATA", os.path.expanduser("~"))
DIR = os.path.join(APPDATA, "harness-discussion")
LOCAL_DIR = os.path.join(LOCALAPPDATA, "harness-discussion")  # the same folder on macOS
PATH = os.path.join(DIR, "settings.json")
TEMP_DIR = os.path.join(tempfile.gettempdir(), "harness-discussion")  # uninstall deletes it
DEFAULT_SOURCES = (["Cursor", "Codex", "Kokoro", "macOS"] if sys.platform == "darwin"
                   else ["Cursor", "Codex", "Kokoro", "Edge", "Windows"])
SYSTEM_VOICES = ("Edge", "Windows", "macOS")
# sources: [name, enabled] in reading order. voices: source name -> voice id, checked by core.
DEFAULTS = {"sources": [[n, True] for n in DEFAULT_SOURCES], "voices": {}, "speed": 1.0,
            "clipboard_fallback": True, "stt_server": False, "prefetch": False,
            "auto_update": sys.platform == "darwin", "caption_font_size": 10,
            "caption_background": False, "caption_background_opacity": 0.6, "ax_granted_build": None,
            "hotkey": hotkeys.DEFAULT,  # "" turns the read shortcut off
            # Writer: rewrite in place. Sources follow the voice order; models and modes are chosen in Settings.
            "writer": False, "writer_sources": [["Cursor", True], ["Codex", True], ["Local", True]],
            "writer_models": {}, "modes": [], "default_mode": "Fix errors",
            "rewrite_hotkey": hotkeys.REWRITE, "dictate_hotkey": hotkeys.DICTATE,
            "stt_sources": [["Cursor", True], ["Codex", True]], "dictate_polish": True,
            "dictate_mode": "Clean dictation"}
WRITER_SOURCES, STT_SOURCES = ("Cursor", "Codex", "Local"), ("Cursor", "Codex")
SPEEDS = (0.5, 2.0)
SPEED_PRESETS = (0.5, 0.75, 1.0, 1.25, 1.5, 1.75, 2.0)  # the popup's speed menu
TASK = "Harness Discussion"  # the installer creates and removes it with `--startup on|off`
# The app was called Select to TTS before 0.5.0, then Codex Speak. migrate() and refresh_startup()
# move their folders and tasks, newest app first, so an older leftover never wins.
LEGACY_TASKS = ("Codex Speak", "Select to TTS")
LEGACY_DIRS = (*(((os.path.join(APPDATA, "codex-speak"), DIR,  # macOS: one folder, so one entry moves it whole
                   ("settings.json", "codex-speak.log", "stt-connection.json")),) if APPDATA == LOCALAPPDATA else
                 ((os.path.join(APPDATA, "codex-speak"), DIR, ("settings.json", "codex-speak.log")),
                  (os.path.join(LOCALAPPDATA, "codex-speak"), LOCAL_DIR, ("stt-connection.json",)))),
               (os.path.join(APPDATA, "select-to-tts"), DIR, ("settings.json",)),
               (os.path.join(LOCALAPPDATA, "select-to-tts"), LOCAL_DIR,
                ("stt-connection.json",)))  # the speech-to-text URL keeps its token
# Earlier builds used this Run value. Windows starts Run entries one at a time after sign-in,
# waiting up to 30 s on each, which delayed the app by minutes.
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_VALUE = "select-to-tts"


def load(path=PATH) -> dict:
    try:
        with open(path, encoding="utf-8") as f:
            saved = json.load(f)
    except (OSError, ValueError):
        saved = {}
    s = {k: saved.get(k, v) for k, v in DEFAULTS.items()} if isinstance(saved, dict) else dict(DEFAULTS)
    sources, seen = [], set()
    for item in s["sources"] if isinstance(s["sources"], list) else []:
        if (isinstance(item, list) and len(item) == 2 and item[0] in DEFAULT_SOURCES
                and item[0] not in seen and isinstance(item[1], bool)):
            seen.add(item[0])
            sources.append(list(item))
    for n in DEFAULT_SOURCES:
        if n not in seen:  # a saved order gains Kokoro ahead of its system voices, other new sources at the end
            at = next((i for i, (m, _) in enumerate(sources) if n == "Kokoro" and m in SYSTEM_VOICES), len(sources))
            sources.insert(at, [n, True])
    legacy = saved.get("engine") if isinstance(saved, dict) else None
    if legacy in DEFAULT_SOURCES and "sources" not in saved:  # Codex Speak saved one engine, or None for Auto
        sources = [[legacy, True]] + [[n, False] for n in DEFAULT_SOURCES if n != legacy]
    s["sources"] = sources
    voices = s["voices"]
    s["voices"] = {k: v for k, v in voices.items() if isinstance(v, str)} if isinstance(voices, dict) else {}
    speed = s["speed"]
    try:
        ok = isinstance(speed, (int, float)) and not isinstance(speed, bool) and math.isfinite(speed)
    except OverflowError:  # an integer too large for a float
        ok = False
    s["speed"] = min(max(float(speed), SPEEDS[0]), SPEEDS[1]) if ok else DEFAULTS["speed"]
    font_size = s["caption_font_size"]
    s["caption_font_size"] = (min(max(font_size, 8), 24)
                               if isinstance(font_size, int) and not isinstance(font_size, bool)
                               else DEFAULTS["caption_font_size"])
    opacity = s["caption_background_opacity"]
    try:
        ok = isinstance(opacity, (int, float)) and not isinstance(opacity, bool) and math.isfinite(opacity)
    except OverflowError:  # an integer too large for a float
        ok = False
    s["caption_background_opacity"] = (min(max(float(opacity), 0.0), 1.0)
                                        if ok else DEFAULTS["caption_background_opacity"])
    for key in ("clipboard_fallback", "stt_server", "prefetch", "caption_background", "auto_update", "dictate_polish"):
        if not isinstance(s[key], bool):
            s[key] = DEFAULTS[key]
    if not isinstance(s["ax_granted_build"], (str, type(None))):
        s["ax_granted_build"] = None
    for key in ("hotkey", "rewrite_hotkey", "dictate_hotkey"):
        if s[key] != "" and not hotkeys.parse(s[key]):
            s[key] = DEFAULTS[key]
    if not isinstance(s["writer"], bool):
        s["writer"] = DEFAULTS["writer"]
    if isinstance(saved, dict) and "writer_sources" not in saved:  # first run of the Writer: follow the voice order
        voice_order = [n for n, _ in s["sources"] if n in WRITER_SOURCES]
        s["writer_sources"] = [[n, True] for n in voice_order + [n for n in WRITER_SOURCES if n not in voice_order]]
    s["writer_sources"] = _ordered(s["writer_sources"], WRITER_SOURCES)
    s["stt_sources"] = _ordered(s["stt_sources"], STT_SOURCES)
    models = s["writer_models"] if isinstance(s["writer_models"], dict) else {}
    s["writer_models"] = {name: {k: v for k, v in choice.items() if k in ("model", "effort") and isinstance(v, str)}
                          for name, choice in models.items() if name in WRITER_SOURCES and isinstance(choice, dict)}
    if not isinstance(s["modes"], list):
        s["modes"] = []
    for key in ("default_mode", "dictate_mode"):
        if not isinstance(s[key], str) or not s[key].strip():
            s[key] = DEFAULTS[key]
    return s


def _ordered(items, names):
    """A saved [[name, enabled]] order: unknown or repeated names dropped, missing ones added enabled at the end."""
    out, seen = [], set()
    for item in items if isinstance(items, list) else []:
        if (isinstance(item, list) and len(item) == 2 and item[0] in names and item[0] not in seen
                and isinstance(item[1], bool)):
            seen.add(item[0])
            out.append(list(item))
    return out + [[n, True] for n in names if n not in seen]


def migrate(dirs=LEGACY_DIRS) -> bool:
    """Moves each old folder to its new name. If it can't, copies the files worth keeping over the
    new ones on every start until a copy of all of them succeeds. Never raises. True if it moved or
    copied anything."""
    moved = False
    for old, new, keep in dirs:
        if not os.path.isdir(old):
            continue
        if not os.path.exists(new):
            try:
                open(os.path.join(old, ".migrated"), "w").close()  # moves atomically with the folder
                os.rename(old, new)
                moved = True
                continue
            except OSError:
                pass  # a file in it is locked: copy instead
        done = os.path.join(new, ".migrated")  # from then on, the new folder's files win
        if os.path.exists(done):
            continue
        try:
            os.makedirs(new, exist_ok=True)
            for name in keep:
                src, dst = os.path.join(old, name), os.path.join(new, name)
                if os.path.exists(src):
                    shutil.copy2(src, dst + ".tmp")
                    os.replace(dst + ".tmp", dst)  # never a half-copied file
            open(done, "w").close()
            moved = True
        except OSError:
            pass  # tried again next start
    return moved


def save(s: dict, path=PATH) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(s, f, indent=2)
    os.replace(tmp, path)  # never leaves a half-written file behind


WELCOME = "Pick the voices Harness Discussion uses."  # Settings header on first run, above Sources
STATUS_TEXT = {"ready": "Ready", "missing": "Not installed", "signed_out": "Not signed in",
               "expired": "Sign-in expired", "not_downloaded": "Not downloaded", "downloading": "Downloading…"}


def status_text(status) -> str:
    """A Settings row's status; None until core's first check finishes."""
    return STATUS_TEXT.get(status, "Checking…")


def speed_label(speed: float) -> str:
    return f"{speed:g}×"


def launch_command() -> tuple[str, str]:
    """(program, arguments) that start this app."""
    if getattr(sys, "frozen", False):
        return sys.executable, ""
    return os.path.join(os.path.dirname(sys.executable), "pythonw.exe"), "-m harness_discussion"


def _scheduler():
    import comtypes.client
    service = comtypes.client.CreateObject("Schedule.Service", dynamic=True)
    service.Connect()
    return service


def startup_enabled(name=TASK) -> bool:
    return bool(_task_enabled(name))


def _task_enabled(name) -> bool | None:
    """None when the task is absent, otherwise its enabled state."""
    try:
        return bool(_scheduler().GetFolder("\\").GetTask(name).Enabled)
    except Exception:  # COMError when the task does not exist
        return None


def set_startup(on: bool, name=TASK) -> None:
    """A logon task for this user only, which needs no admin rights. Also drops the old Run value."""
    from comtypes import COMError
    try:
        service = _scheduler()
        folder = service.GetFolder("\\")
        if on:
            user = f"{os.environ['USERDOMAIN']}\\{os.environ['USERNAME']}"
            d = _logon_task(service, user, *launch_command())
            folder.RegisterTaskDefinition(name, d, 6, user, None, 3)  # create or update, interactive
        else:
            installed = name == TASK and getattr(sys, "frozen", False)  # source runs keep the old task
            for task in (name, *LEGACY_TASKS) if installed else (name,):
                if _task_enabled(task) is not None:
                    folder.DeleteTask(task, 0)
    except COMError as e:
        raise OSError(str(e)) from e
    if name == TASK:
        _drop_run_value()


def _logon_task(service, user, program, args):
    d = service.NewTask(0)
    d.RegistrationInfo.Description = "Starts Harness Discussion when you sign in."
    trigger = d.Triggers.Create(9)  # TASK_TRIGGER_LOGON
    trigger.UserId = user
    action = d.Actions.Create(0)  # TASK_ACTION_EXEC
    action.Path, action.Arguments, action.WorkingDirectory = program, args, os.path.dirname(program)
    d.Settings.DisallowStartIfOnBatteries = False
    d.Settings.StopIfGoingOnBatteries = False
    d.Settings.ExecutionTimeLimit = "PT0S"  # the default would stop the app after 72 hours
    d.Settings.Priority = 5  # the default 7 runs below normal priority
    return d


def _drop_run_value():
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
            winreg.DeleteValue(k, RUN_VALUE)
    except OSError:
        pass  # not there


def refresh_startup() -> None:
    """Points an enabled sign-in task at this install (it may have moved) and moves an older
    build's Run value or Codex Speak or Select to TTS task to the task. Source runs leave an installed
    copy's task alone."""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            winreg.QueryValueEx(k, RUN_VALUE)
        legacy = True
    except OSError:
        legacy = False
    frozen = getattr(sys, "frozen", False)
    if not frozen:
        return
    old_tasks = {name: state for name in LEGACY_TASKS if (state := _task_enabled(name)) is not None}
    enabled = _task_enabled(TASK)
    if enabled is None:
        enabled = next(iter(old_tasks.values()), legacy)
    if enabled:
        set_startup(True)
    for name in old_tasks:  # a disabled one is dropped, not carried over
        set_startup(False, name)
    if legacy and not enabled:
        _drop_run_value()
