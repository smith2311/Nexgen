from django.urls import path

from . import views

app_name = "billing"

urlpatterns = [
    path("", views.home, name="home"),
    path("upload/", views.upload, name="upload"),
    path("uploads/<int:pk>/", views.upload_detail, name="upload_detail"),
    path("uploads/<int:pk>/download/", views.download_upload, name="download_upload"),
    path("uploads/<int:pk>/status/", views.upload_status, name="upload_status"),
    path("uploads/<int:pk>/delete/", views.delete_upload, name="delete_upload"),

    path("dashboard/", views.dashboard, name="dashboard"),
    path("api/dashboard/", views.dashboard_data_api, name="dashboard_data_api"),

    path("data/", views.data_grid, name="data_grid"),
    path("export/", views.export_xlsx, name="export"),
]
