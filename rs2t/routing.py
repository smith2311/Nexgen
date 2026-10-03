from django.urls import re_path

from .consumers import TranslatorConsumer

websocket_urlpatterns = [
    re_path(r"^ws/rs2t/$", TranslatorConsumer.as_asgi()),
]
