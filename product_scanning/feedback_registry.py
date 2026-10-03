"""
feedback_registry.py
─────────────────────────────────────────────────────────────────────────────
Self-registering module registry for the NexGen Feedback system.

How it works
  Each app registers itself once in its AppConfig.ready():

      from product_scanning.feedback_registry import register_module

      class MyAppConfig(AppConfig):
          def ready(self):
              register_module(
                  value  = "my_app",       # stored in DB
                  label  = "My App Name",  # shown in the dropdown
                  icon   = "🚀",
                  groups = {"My Group A", "My Group B"},  # who can see it
                  order  = 50,             # position in dropdown (lower = first)
              )

  • groups=set()  (empty)  → visible to ALL authenticated users.
  • groups={'A','B'}       → visible only to members of group A or B (or superuser).
  • order                  → controls sort order in the dropdown; defaults to 100.

  "Other / General" and "Login / Access" are always present (registered by
  product_scanning itself) and are visible to everyone.

Adding a new app in the future
  1. Add 3 lines to your app's AppConfig.ready()  ← that's it.
  2. No model changes, no migrations, no changes to the feedback system.
"""

import logging
import threading

logger = logging.getLogger(__name__)

_lock     = threading.Lock()
_registry = []   # list of dicts, ordered by insertion


# ── Public API ───────────────────────────────────────────────────────────────

def register_module(value, label, icon="📌", groups=None, order=100):
    """
    Register an application module so it appears in the feedback form.

    Parameters
    ----------
    value  : str   – stored in the DB field (max 30 chars, lower_snake_case)
    label  : str   – human-readable name shown in the dropdown
    icon   : str   – emoji or character shown next to the label (optional)
    groups : set   – Django group names whose members can see this module.
                     Pass an empty set (or omit) to make it visible to everyone.
    order  : int   – lower = appears earlier in the dropdown (default 100)
    """
    if not isinstance(value, str) or len(value) > 30:
        raise ValueError(f"register_module: 'value' must be a string ≤ 30 chars, got {value!r}")

    groups = set(groups) if groups else set()

    with _lock:
        # Prevent duplicate registration (e.g. on dev server double-reload)
        if any(m["value"] == value for m in _registry):
            return
        _registry.append({
            "value":  value,
            "label":  label,
            "icon":   icon,
            "groups": groups,
            "order":  order,
        })
        _registry.sort(key=lambda m: (m["order"], m["label"]))
        logger.debug("feedback_registry: registered module %r (%s)", value, label)


def get_all_choices():
    """Return all registered modules as [(value, label), …] tuples."""
    with _lock:
        return [(m["value"], m["label"]) for m in list(_registry)]


def get_choices_for_user(user):
    """
    Return [(value, label), …] filtered to this user's accessible modules.
    Superusers and staff always receive all modules.
    """
    if user.is_superuser or user.is_staff:
        return get_all_choices()

    user_groups = set(user.groups.values_list("name", flat=True))

    with _lock:
        result = []
        for m in _registry:
            # Empty groups set → visible to everyone
            if not m["groups"] or m["groups"] & user_groups:
                result.append((m["value"], m["label"]))
        return result


def get_label(value):
    """Return the human-readable label for a stored module value."""
    with _lock:
        for m in _registry:
            if m["value"] == value:
                return m["label"]
    return value.replace("_", " ").title()


def get_icon(value):
    """Return the icon for a stored module value."""
    with _lock:
        for m in _registry:
            if m["value"] == value:
                return m["icon"]
    return "📌"


def get_all_modules():
    """Return the full registry list (for admin/debug use)."""
    with _lock:
        return list(_registry)


def is_registered(value):
    """Return True if the value is already in the registry."""
    with _lock:
        return any(m["value"] == value for m in _registry)
