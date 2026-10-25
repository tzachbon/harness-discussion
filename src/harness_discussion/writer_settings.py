"""What Settings → Writing and Dictation change, as plain values both platforms can share. Each function returns a
new value for one settings key and never mutates its input."""
from . import writer

LOCAL_MODEL = "Qwen3-4B"


def move(order, name, step):
    order = [list(item) for item in order]
    i = [n for n, _ in order].index(name)
    if 0 <= i + step < len(order):
        order[i], order[i + step] = order[i + step], order[i]
    return order


def toggle(order, name, on):
    return [[n, on if n == name else was] for n, was in order]


def model_choices(name, models):
    """[(id, label)] for the model menu. "" is the source's own default. Local runs one fixed model."""
    if name == "Local":
        return [("", LOCAL_MODEL)]
    listed = [(m["id"], m.get("label") or m["id"]) for m in models.get(name, ()) if m.get("id")]
    return [("", "Default")] + listed


def effort_choices(name, models, model_id):
    """Efforts the chosen model accepts, or [] when there is nothing to choose."""
    for m in models.get(name, ()):
        if m.get("id") == model_id:
            return [e for e in m.get("efforts") or () if isinstance(e, str)]
    return []


def set_model(chosen, name, model, effort, models):
    """The writer_models value with `name` set. An effort the model doesn't accept is dropped."""
    choice = {}
    if model:
        choice["model"] = model
        if effort and effort in effort_choices(name, models, model):
            choice["effort"] = effort
    out = {k: dict(v) for k, v in chosen.items() if k != name}
    if choice:
        out[name] = choice
    return out


def chosen_model(chosen, name):
    choice = chosen.get(name) or {}
    return choice.get("model", ""), choice.get("effort", "")


def add_mode(custom):
    """(modes, new name): a new custom mode with a free name."""
    names = {m.name for m in writer.all_modes(custom)}
    name = next(n for n in ("New mode" if i == 1 else f"New mode {i}" for i in range(1, 1000)) if n not in names)
    return [*custom, {"name": name, "instructions": "Describe how to rewrite the text."}], name


def save_mode(custom, old_name, name, instructions):
    """(modes, saved name, error). Built-in modes keep their name. Editing one stores an override with that name."""
    name, instructions = name.strip(), instructions.strip()
    if old_name in {m.name for m in writer.BUILTIN_MODES}:
        name = old_name
    if not name or not instructions:
        return custom, old_name, "A mode needs a name and instructions."
    if name != old_name and name in {m.name for m in writer.all_modes(custom)}:
        return custom, old_name, f"There is already a mode called {name}."
    out = [dict(m) for m in custom if m.get("name") != old_name]
    i = next((k for k, m in enumerate(custom) if m.get("name") == old_name), len(out))
    out.insert(i, {"name": name, "instructions": instructions})
    return out, name, None


def delete_mode(custom, name):
    """Removes a custom mode. For a built-in, removes the override, which restores the original."""
    return [dict(m) for m in custom if m.get("name") != name]


def default_mode(default, custom, fallback="Fix errors"):
    """The selected mode, or its fallback when the saved one was deleted or renamed."""
    names = [m.name for m in writer.all_modes(custom)]
    return default if default in names else fallback
