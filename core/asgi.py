import os

from django.core.asgi import get_asgi_application

from channels.auth import AuthMiddlewareStack
from channels.routing import ProtocolTypeRouter, URLRouter

from rs2t.routing import websocket_urlpatterns

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "core.settings")

application = ProtocolTypeRouter(
    {
        "http": get_asgi_application(),
        # AllowedHostsOriginValidator removed — nginx already enforces the domain
        # via TLS + server_name. Session auth (AuthMiddlewareStack) handles identity.
        "websocket": AuthMiddlewareStack(URLRouter(websocket_urlpatterns)),
    }
)
