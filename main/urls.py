from django.conf import settings
from django.conf.urls.static import static
from django.urls import include, path

from . import views

urlpatterns = [
    # Authentication
    path("", views.TurnstileLoginView.as_view(), name="login"),
    path("register/", views.register_view, name="register"),
    path("logout/", views.custom_logout, name="logout"),
    path("dashboard/", views.landing_page, name="landing_page"),
    path("profile/", views.profile_view, name="profile"),
    path("profile/update/", views.profile_update, name="profile_update"),
    path("profile/settings/", views.settings_update, name="settings_update"),
    # SSO OAuth — safe wrapper intercepts /oauth/complete/<backend>/ first so that
    # expired/replayed one-time codes never crash the Daphne process.
    path("oauth/complete/<str:backend>/", views.safe_oauth_complete),
    # Overrides social_django's own begin view (declared before the include
    # below, same path) so a stale session["next"] cannot be replayed. See
    # views.oauth_begin_clean.
    path("oauth/login/<str:backend>/", views.oauth_begin_clean),
    path("oauth/", include("social_django.urls", namespace="social")),
    path("rs2t/", include("rs2t.urls")),
]

# Serve media files in development
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)
