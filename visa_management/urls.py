from django.urls import path

from . import views

app_name = 'visa'

urlpatterns = [
    # ── Home (role-based redirect) ────────────────────────────────────────
    path('', views.visa_home, name='home'),

    # ── Employee ──────────────────────────────────────────────────────────
    path('apply/',                       views.apply_new,          name='apply_new'),
    path('apply/<int:pk>/edit/',         views.apply_edit,         name='apply_edit'),
    path('my-applications/',             views.my_applications,    name='my_applications'),
    path('application/<int:pk>/',        views.application_detail, name='application_detail'),

    # ── HOD ───────────────────────────────────────────────────────────────
    path('hod/',                                          views.hod_dashboard,    name='hod_dashboard'),
    path('hod/application/<int:pk>/',                     views.hod_review,       name='hod_review'),
    path('hod/application/<int:pk>/approve/',             views.hod_approve,      name='hod_approve'),
    path('hod/application/<int:pk>/reject/',              views.hod_reject,       name='hod_reject'),

    # ── Visa Department ───────────────────────────────────────────────────
    path('admin/',                                        views.admin_dashboard,   name='admin_dashboard'),
    path('admin/application/<int:pk>/',                   views.admin_detail,      name='admin_detail'),
    path('admin/application/<int:pk>/save-letter/',       views.admin_save_letter, name='admin_save_letter'),
    path('admin/application/<int:pk>/switch-letter/<int:template_pk>/', views.admin_switch_letter_jurisdiction, name='admin_switch_letter_jurisdiction'),
    path('admin/application/<int:pk>/print/',             views.admin_print,       name='admin_print'),
    path('admin/application/<int:pk>/approve/',           views.admin_approve,     name='admin_approve'),
    path('admin/application/<int:pk>/reject/',            views.admin_reject,      name='admin_reject'),

    # ── Letter Templates ──────────────────────────────────────────────────
    path('admin/templates/',               views.template_list,    name='template_list'),
    path('admin/templates/create/',        views.template_create,  name='template_create'),
    path('admin/templates/<int:pk>/edit/', views.template_edit,    name='template_edit'),

    # ── Country master ────────────────────────────────────────────────────
    path('admin/countries/',               views.country_list,     name='country_list'),
    path('admin/countries/create/',        views.country_create,   name='country_create'),
    path('admin/countries/<int:pk>/edit/', views.country_edit,     name='country_edit'),

    # ── User master ───────────────────────────────────────────────────────
    path('admin/users/',                   views.user_master,             name='user_master'),
    path('admin/users/template/',          views.user_template_download,  name='user_template_download'),

    # ── Company master ────────────────────────────────────────────────────
    path('admin/companies/',               views.company_list,     name='company_list'),
    path('admin/companies/create/',        views.company_create,   name='company_create'),
    path('admin/companies/<int:pk>/edit/', views.company_edit,     name='company_edit'),
]
