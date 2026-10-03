"""
ui/context_processors.py
Turns the components marked by @uses_components into template variables.

Exposes two lists to every template:
    ui_css  -> list of CSS paths to load in <head>
    ui_js   -> list of JS paths to load at the bottom of the page

Safe by default: if a view did not opt in, both lists are empty, so pages
that don't use the system render exactly as before.
"""
from ui.registry import COMPONENTS


def ui_assets(request):
    names = getattr(request, "ui_components", [])
    css, js, seen = [], [], set()
    for name in names:
        comp = COMPONENTS.get(name, {})
        for f in comp.get("css", []):
            if f not in seen:
                seen.add(f)
                css.append(f)
        for f in comp.get("js", []):
            if f not in seen:
                seen.add(f)
                js.append(f)
    return {"ui_css": css, "ui_js": js}
