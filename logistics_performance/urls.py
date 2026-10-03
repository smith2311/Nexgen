from django.urls import path
from . import views

app_name = 'logistics'

urlpatterns = [
    path('', views.logistics_home, name='home'),
    path('dashboard/', views.master_dashboard, name='master_dashboard'),

    # Location master (pincode master)
    path('location-master/', views.pincode_master_list, name='location_master_list'),
    path('pincodes/', views.pincode_master_list, name='pincode_master_list'),
    path('pincodes/create/', views.pincode_master_create, name='pincode_master_create'),
    path('pincodes/upload/', views.pincode_master_upload, name='pincode_master_upload'),
    path('pincodes/<int:pk>/', views.pincode_master_detail, name='pincode_master_detail'),
    path('pincodes/<int:pk>/edit/', views.pincode_master_edit, name='pincode_master_edit'),

    # Domestic sales type master
    # Domestic Sales Type Master — page
    path('domestic-sales-type-master/', views.domestic_sales_list, name='domestic_sales_type_master_list'),

    # Domestic Sales Type Master — APIs
    path('api/sales/list/',             views.sales_list_api,    name='sales_list_api'),
    path('api/sales/create/',           views.sales_create_api,  name='sales_create_api'),
    path('api/sales/update/<int:id>/',  views.sales_update_api,  name='sales_update_api'),
    path('api/sales/detail/<int:id>/',  views.sales_detail_api,  name='sales_detail_api'),
    path('api/sales/delete/<int:id>/',  views.sales_delete_api,  name='sales_delete_api'),
    path('api/sales/upload/',           views.sales_upload_api,  name='sales_upload_api'),
    path('api/sales/preview/',          views.sales_preview_api, name='sales_preview_api'),
    path('api/sales/confirm/',          views.sales_confirm_api, name='sales_confirm_api'),
    # Location Master APIs
    path(
        'api/location/list/',
        views.location_list_api,
        name='location_list_api'
    ),

    path(
        'api/location/create/',
        views.location_create_api,
        name='location_create_api'
    ),

    path(
        'api/location/update/<int:id>/',
        views.location_update_api,
        name='location_update_api'
    ),

    path(
        'api/location/delete/<int:id>/',
        views.location_delete_api,
        name='location_delete_api'
    ),

    path(
        'api/location/upload/',
        views.location_upload_api,
        name='location_upload_api'
    ),

    path(
        'api/location/preview/',
        views.location_preview_api,
        name='location_preview_api'
    ),

    path(
        'api/location/confirm/',
        views.location_confirm_api,
        name='location_confirm_api'
    ),
    # Courier Master — page
    path('courier-master/', views.courier_master_list, name='courier_master_list'),

    # Courier Master APIs
    path('api/courier/list/',            views.courier_list_api,    name='courier_list_api'),
    path('api/courier/create/',          views.courier_create_api,  name='courier_create_api'),
    path('api/courier/update/<int:id>/', views.courier_update_api,  name='courier_update_api'),
    path('api/courier/delete/<int:id>/', views.courier_delete_api,  name='courier_delete_api'),
    path('api/courier/upload/',          views.courier_upload_api,  name='courier_upload_api'),
    path('api/courier/preview/',         views.courier_preview_api, name='courier_preview_api'),
    path('api/courier/confirm/',         views.courier_confirm_api, name='courier_confirm_api'),

    # Zone Master — page
    path('zone-master/', views.zone_master_list, name='zone_master_list'),

    # Zone Master APIs
    path('api/zone/list/',            views.zone_list_api,    name='zone_list_api'),
    path('api/zone/create/',          views.zone_create_api,  name='zone_create_api'),
    path('api/zone/update/<int:id>/', views.zone_update_api,  name='zone_update_api'),
    path('api/zone/delete/<int:id>/', views.zone_delete_api,  name='zone_delete_api'),
    path('api/zone/upload/',          views.zone_upload_api,  name='zone_upload_api'),
    path('api/zone/preview/',         views.zone_preview_api, name='zone_preview_api'),
    path('api/zone/confirm/',         views.zone_confirm_api, name='zone_confirm_api'),

    # Mode Master — page
    path('mode-master/', views.mode_master_list, name='mode_master_list'),

    # Mode Master APIs
    path('api/mode/list/',            views.mode_list_api,    name='mode_list_api'),
    path('api/mode/create/',          views.mode_create_api,  name='mode_create_api'),
    path('api/mode/update/<int:id>/', views.mode_update_api,  name='mode_update_api'),
    path('api/mode/delete/<int:id>/', views.mode_delete_api,  name='mode_delete_api'),
    path('api/mode/upload/',          views.mode_upload_api,  name='mode_upload_api'),
    path('api/mode/preview/',         views.mode_preview_api, name='mode_preview_api'),
    path('api/mode/confirm/',         views.mode_confirm_api, name='mode_confirm_api'),

    # State Master — page
    path('state-master/', views.state_master_list, name='state_master_list'),

    # State Master APIs
    path('api/state/list/',            views.state_list_api,    name='state_list_api'),
    path('api/state/create/',          views.state_create_api,  name='state_create_api'),
    path('api/state/update/<int:id>/', views.state_update_api,  name='state_update_api'),
    path('api/state/delete/<int:id>/', views.state_delete_api,  name='state_delete_api'),
    path('api/state/upload/',          views.state_upload_api,  name='state_upload_api'),
    path('api/state/preview/',         views.state_preview_api, name='state_preview_api'),
    path('api/state/confirm/',         views.state_confirm_api, name='state_confirm_api'),

    # Plant Master — page
    path('plant-master/', views.plant_master_list, name='plant_master_list'),

    # Plant Master APIs
    path('api/plant/list/',            views.plant_list_api,    name='plant_list_api'),
    path('api/plant/create/',          views.plant_create_api,  name='plant_create_api'),
    path('api/plant/update/<int:id>/', views.plant_update_api,  name='plant_update_api'),
    path('api/plant/delete/<int:id>/', views.plant_delete_api,  name='plant_delete_api'),
    path('api/plant/upload/',          views.plant_upload_api,  name='plant_upload_api'),
    path('api/plant/preview/',         views.plant_preview_api, name='plant_preview_api'),
    path('api/plant/confirm/',         views.plant_confirm_api, name='plant_confirm_api'),

    # Division Master — page
    path('division-master/', views.division_master_list, name='division_master_list'),

    # Division Master APIs
    path('api/division/list/',            views.division_list_api,    name='division_list_api'),
    path('api/division/create/',          views.division_create_api,  name='division_create_api'),
    path('api/division/update/<int:id>/', views.division_update_api,  name='division_update_api'),
    path('api/division/delete/<int:id>/', views.division_delete_api,  name='division_delete_api'),
    path('api/division/upload/',          views.division_upload_api,  name='division_upload_api'),
    path('api/division/preview/',         views.division_preview_api, name='division_preview_api'),
    path('api/division/confirm/',         views.division_confirm_api, name='division_confirm_api'),

    # Logistics Performance page
    path('logistics-performance-upload/', views.logistics_performance_list, name='logistics_performance_list'),
    # Logistics Performance — single record detail page
    path('logistics-performance/<int:pk>/', views.lp_detail_page, name='lp_detail_page'),
    # Logistics Performance APIs
    path('api/lp/list/',                       views.lp_list_api,                    name='lp_list_api'),
    path('api/lp/create/',                     views.lp_create_api,                  name='lp_create_api'),
    path('api/lp/update/<int:id>/',            views.lp_update_api,                  name='lp_update_api'),
    path('api/lp/delete/<int:id>/',            views.lp_delete_api,                  name='lp_delete_api'),
    path('api/lp/bulk-delete/',                views.lp_bulk_delete_api,             name='lp_bulk_delete_api'),
    path('api/lp/upload/',                     views.lp_upload_api,                  name='lp_upload_api'),
    path('api/lp/preview/',                    views.lp_preview_api,                 name='lp_preview_api'),
    path('api/lp/confirm/',                    views.lp_confirm_api,                 name='lp_confirm_api'),
    # Pending records
    path('api/lp/pending/',                    views.lp_pending_api,                 name='lp_pending_api'),
    path('api/lp/pending/export/',             views.lp_pending_export_api,          name='lp_pending_export_api'),
    path('api/lp/pending/update-upload/',      views.lp_pending_update_upload_api,   name='lp_pending_update_upload_api'),
    path('api/lp/pending/update-confirm/',     views.lp_pending_update_confirm_api,  name='lp_pending_update_confirm_api'),

    # ── Logistics Performance Report ──────────────────────────────────────────
    path('performance-report/',             views.logistics_performance_report_page, name='lp_report_page'),
    path('api/report/stats/',               views.report_stats_api,           name='report_stats_api'),
    path('api/report/tat/',                 views.report_tat_api,             name='report_tat_api'),
    path('api/report/cost/',                views.report_cost_api,            name='report_cost_api'),
    path('api/report/courier/',             views.report_courier_api,         name='report_courier_api'),
    path('api/report/lc/',                  views.report_lc_api,              name='report_lc_api'),
    path('api/report/pincode/',             views.report_pincode_api,         name='report_pincode_api'),
    # Drill-down detail APIs
    path('api/report/tat/detail/',          views.report_tat_detail_api,      name='report_tat_detail_api'),
    path('api/report/courier/detail/',      views.report_courier_detail_api,  name='report_courier_detail_api'),
    # Export APIs
    path('api/report/tat/export/',          views.report_tat_export,          name='report_tat_export'),
    path('api/report/cost/export/',         views.report_cost_export,         name='report_cost_export'),
    path('api/report/courier/export/',      views.report_courier_export,      name='report_courier_export'),
    path('api/report/lc/export/',           views.report_lc_export,           name='report_lc_export'),
]
