"""
ui/decorators.py
View decorator to declare which front-end components a page uses.

Usage:
    from ui.decorators import uses_components

    @uses_components("adv_filter", "data_table")
    def my_view(request):
        ...

The decorator only marks the request. ui.context_processors.ui_assets reads
that mark and exposes the resolved css/js file lists to the template.
"""
from functools import wraps


def uses_components(*names):
    def decorator(view_func):
        @wraps(view_func)
        def wrapper(request, *args, **kwargs):
            existing = getattr(request, "ui_components", [])
            request.ui_components = list(existing) + list(names)
            return view_func(request, *args, **kwargs)
        return wrapper
    return decorator
