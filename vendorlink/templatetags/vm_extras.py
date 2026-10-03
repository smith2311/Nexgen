from django import template

register = template.Library()


@register.filter
def attr(obj, name):
    """Whether ``obj`` declares the capability ``name``.

    Named ``attr`` because it began as a plain ``getattr`` over the nine
    capability ``BooleanField``s, and the capability matrix, vendor list and
    vendor detail templates all call it that way.

    Those columns no longer exist — capabilities are rows in the Capability
    master, linked by M2M — so a bare getattr here would return False for every
    vendor and every one of those screens would render a fully-capable vendor as
    having nothing, with no error anywhere. Routed through ``has_capability``,
    which reads the links and caches them per instance.

    Anything that is not a capability holder still gets plain attribute access,
    so the filter keeps doing what its name says.
    """
    if hasattr(obj, "has_capability"):
        return obj.has_capability(name)
    return getattr(obj, name, False)


@register.filter
def has_cap(obj, code):
    """Explicit alias for :func:`attr`. Preferred in new templates."""
    return attr(obj, code)
