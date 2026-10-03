from django.urls import path

from . import views

app_name = "rs2t"

urlpatterns = [
    path("", views.index, name="index"),
    path("history/", views.history_list, name="history"),
    path("glossary/", views.glossary_list, name="glossary"),
    path("glossary/add/", views.glossary_add, name="glossary_add"),
    path("glossary/<int:pk>/delete/", views.glossary_delete, name="glossary_delete"),
    path("history/<int:pk>/", views.session_detail, name="session_detail"),
    path("history/<int:pk>/download/", views.session_download, name="session_download"),
    path("history/<int:pk>/delete/", views.session_delete, name="session_delete"),
]
