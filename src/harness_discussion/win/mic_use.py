"""Read Windows microphone usage; stale entries can survive an app crash."""
import ntpath
import sys


CONSENT = r"Software\Microsoft\Windows\CurrentVersion\CapabilityAccessManager\ConsentStore\microphone"


def _in_use(entries: dict, own: set[str]) -> set[str]:
    own = {path.lower() for path in own}
    result = set()
    for name, values in entries.get("packaged", {}).items():
        if values.get("LastUsedTimeStop") == 0:
            result.add(name.lower())
    for name, values in entries.get("nonpackaged", {}).items():
        path = name.replace("#", "\\").lower()
        if values.get("LastUsedTimeStop") == 0 and path not in own:
            result.add(ntpath.basename(path))
    return result


def users(own=None) -> set[str]:
    import winreg

    if own is None:
        own = {sys.executable, getattr(sys, "_base_executable", sys.executable)}
    entries = {"packaged": {}, "nonpackaged": {}}
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, CONSENT) as root:
        def read_children(parent, target, skip_nonpackaged=False):
            for index in range(winreg.QueryInfoKey(parent)[0]):
                name = winreg.EnumKey(parent, index)
                if skip_nonpackaged and name.lower() == "nonpackaged":
                    continue
                try:
                    with winreg.OpenKey(parent, name) as key:
                        stop, kind = winreg.QueryValueEx(key, "LastUsedTimeStop")
                    if kind == winreg.REG_QWORD:
                        target[name] = {"LastUsedTimeStop": stop}
                except FileNotFoundError:
                    continue

        read_children(root, entries["packaged"], True)
        try:
            with winreg.OpenKey(root, "NonPackaged") as nonpackaged:
                read_children(nonpackaged, entries["nonpackaged"])
        except FileNotFoundError:
            pass
    # ponytail: no process check; add one if crashed apps leave stale active entries.
    return _in_use(entries, own)
