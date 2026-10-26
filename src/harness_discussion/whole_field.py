"""The rewrite shortcut with nothing selected rewrites the whole focused field."""
from . import live

MAX = 4_000  # UTF-16 units; a longer field needs a selection


def select(edit):
    """Selects all of the focused field when nothing in it is selected: (field, caret before). False when there is
    a selection or no readable field, so the selection flow runs as before. None when the field is longer than MAX."""
    observe, select_range = getattr(edit, "observe", None), getattr(edit, "select_range", None)
    field = observe() if observe and select_range else None
    if field is None or field.caret[0] != field.caret[1] or not field.text.strip():
        return False
    if live.units(field.text) > MAX:
        return None
    return (field, field.caret[0]) if select_range(field, 0, live.units(field.text), field.text) is not None else False


def restore(edit, whole):
    """After a rewrite that pasted nothing, puts the caret back, unless the field or its selection moved."""
    field, at = whole
    now = edit.observe()
    if now is not None and (now.app, now.window, now.digest, now.caret) == (
            field.app, field.window, field.digest, (0, live.units(field.text))):
        edit.select_range(now, at, at, "")
