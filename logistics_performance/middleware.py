import threading

# Per-request store for the logged-in username, so models can auto-stamp
# created_by / updated_by without each view passing the user explicitly.
_current = threading.local()


def get_current_username():
    return getattr(_current, 'username', None)


def set_current_username(username):
    """
    Override the stashed username part-way through a request.

    CurrentUserMiddleware below reads request.user before the view runs, which
    only resolves for session-authenticated requests. DRF authenticates later,
    so a token-authenticated API call is still anonymous when the middleware
    looks — leaving created_by / updated_by blank. A DRF view calls this once
    authentication has resolved the real user.
    """
    _current.username = username or None


class CurrentUserMiddleware:
    """Stashes the logged-in username for the duration of each request."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, 'user', None)
        _current.username = user.username if (user and user.is_authenticated) else None
        try:
            return self.get_response(request)
        finally:
            _current.username = None
