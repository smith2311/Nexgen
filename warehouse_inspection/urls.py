"""
Warehouse Inspection URL Configuration
────────────────────────────────────────────────────────────────
URL routing for warehouse inspection module.
"""
from django.urls import path, include
from rest_framework.routers import DefaultRouter
from . import views

app_name = 'warehouse_inspection'

# REST API Router
router = DefaultRouter()
router.register(
    r'buildings',
    views.WarehouseInspectionBuildingViewSet,
    basename='building'
)
router.register(
    r'conditions',
    views.ConditionCategoryViewSet,
    basename='condition'
)
router.register(
    r'floors',
    views.WarehouseInspectionFloorViewSet,
    basename='floor'
)
router.register(
    r'floor-assignments',
    views.WarehouseInspectionFloorAssignmentViewSet,
    basename='floor-assignment'
)
router.register(
    r'checkpoints',
    views.WarehouseInspectionCheckpointViewSet,
    basename='checkpoint'
)
router.register(
    r'responses',
    views.WarehouseInspectionResponseViewSet,
    basename='response'
)
router.register(
    r'submissions',
    views.WarehouseInspectionSubmissionViewSet,
    basename='submission'
)
router.register(
    r'second-submissions',
    views.WarehouseInspectionSecondSubmissionViewSet,
    basename='second-submission'
)

# URL Patterns
urlpatterns = [
    # API endpoints
    path(
        'api/checkpoint-photos/',
        views.WarehouseInspectionResponsePhotoUploadView.as_view(),
        name='checkpoint_photo_upload',
    ),
    # Scheduled-email trigger for n8n / any external scheduler. Declared
    # before the router include so it is not shadowed by a viewset route.
    path(
        'api/run-daily-emails/',
        views.WarehouseInspectionDailyEmailAPIView.as_view(),
        name='run_daily_emails',
    ),
    path('api/', include(router.urls)),

    # Frontend views
    path('', views.inspection_dashboard, name='dashboard'),
    path('conditions/', views.condition_management, name='conditions'),
    path('inspect/', views.inspection_form, name='inspection_form'),
    path('admin/users/', views.admin_user_list, name='admin_user_list'),
    path('admin/users/create/', views.create_user_access, name='admin_create_user_access'),
    path('admin/users/update-role/', views.update_user_role, name='admin_update_user_role'),
    path('admin/users/remove-access/', views.remove_user_access, name='admin_remove_user_access'),
    path('admin/report-recipients/', views.report_recipients_page, name='report_recipients_page'),
    path('admin/report-recipients/list/', views.report_recipients_list, name='report_recipients_list'),
    path('admin/report-recipients/add/', views.add_report_recipient, name='add_report_recipient'),
    path('admin/report-recipients/remove/', views.remove_report_recipient, name='remove_report_recipient'),
    path('import-checklist/', views.import_checklist_page, name='import_checklist'),
    path('import-checklist/preview/', views.import_checklist_preview, name='import_checklist_preview'),
    path('import-checklist/confirm/', views.import_checklist_confirm, name='import_checklist_confirm'),
]
