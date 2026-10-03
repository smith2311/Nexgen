from django.urls import path
from . import views

app_name = 'material_rework'

urlpatterns = [
    path('', views.dashboard, name='dashboard'),
    path('upload/', views.upload_view, name='upload'),
    path('upload/template/', views.download_upload_template, name='upload_template'),
    path('my-uploads/', views.my_uploads, name='my_uploads'),
    path('hod-review/<uuid:token>/', views.hod_review, name='hod_review'),
    path('production/', views.production_list, name='production_list'),
    path('production/<int:batch_id>/', views.production_review_batch, name='production_review_batch'),
    path('warehouse/', views.warehouse_view, name='warehouse'),
    # The rework round trip: warehouse releases -> production returns ->
    # warehouse counts. Per-line rather than per-batch, because the legs of one
    # batch genuinely complete at different times.
    path('rework/<int:item_id>/release/', views.rework_release, name='rework_release'),
    path('rework/<int:item_id>/return/', views.rework_return, name='rework_return'),
    path('rework/<int:item_id>/receive/', views.rework_receive, name='rework_receive'),
    path('api/warehouse-status/', views.warehouse_status, name='warehouse_status'),
    path('report/', views.report_view, name='report'),
    path('report/download/', views.report_download, name='report_download'),
    path('masters/', views.masters_view, name='masters'),
    path('masters/template/verticals/', views.download_template_verticals, name='template_verticals'),
    path('masters/template/departments/', views.download_template_departments, name='template_departments'),
    path('api/departments/<int:vertical_id>/', views.departments_ajax, name='departments_ajax'),
    path('delete/<int:batch_id>/', views.delete_batch, name='delete_batch'),
    path('replace/<int:batch_id>/', views.replace_batch, name='replace_batch'),
    path('save-upload-session/', views.save_upload_session, name='save_upload_session'),
    path('hod-inbox/', views.hod_inbox, name='hod_inbox'),
    # The Order Management gate. A flow can route through OM before PPIC —
    # the seeded Warehouse flow does — so it gets the same inbox + review pair.
    path('om/', views.om_list, name='om_list'),
    path('om/<int:batch_id>/', views.om_review_batch, name='om_review_batch'),
    path('ppic/', views.ppic_list, name='ppic_list'),
    path('ppic/<int:batch_id>/', views.ppic_review_batch, name='ppic_review_batch'),
    # Where the flows themselves are decided.
    path('masters/routing/', views.routing_rules, name='routing_rules'),
    path('batch/<int:batch_id>/print-labels/', views.print_labels, name='print_labels'),
    path('users/', views.md_users_view, name='md_users'),
    path('users/template/', views.download_md_users_template, name='md_users_template'),
    path('api/my-uploads-status/', views.my_uploads_status, name='my_uploads_status'),
]
