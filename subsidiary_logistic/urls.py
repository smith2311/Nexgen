from django.urls import path
from . import views

app_name = "sub"

urlpatterns = [
    path("",                    views.home,              name="home"),
    path("dashboard/",          views.dashboard,         name="dashboard"),
    path("upload/",             views.upload,            name="upload"),
    path("upload/preview/",     views.upload_preview,    name="upload_preview"),
    path("template/download/",  views.template_download, name="template_download"),
]
