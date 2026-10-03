from django.urls import path

from . import views

app_name = 'pr_tracker'

urlpatterns = [
    # Home
    path('',                           views.pr_home,                  name='home'),

    # Manager
    path('dashboard/',                 views.pr_dashboard,             name='pr_dashboard'),
    path('assign/',                    views.pr_assign,                name='pr_assign'),
    path('<int:pk>/',                  views.pr_detail,                name='pr_detail'),
    # The target of every document link in an assignment email. Served
    # through a view, not MEDIA_URL, so the file sits behind the login —
    # email_utils builds this path, so the two must stay in step.
    path('document/<int:pk>/',         views.pr_document_download,     name='pr_document_download'),
    # Same file, rendered in place instead of downloaded — the eye icon
    # on the PR detail screen. Only types a browser can draw; see
    # PR_DOC_PREVIEW_TYPES.
    path('document/<int:pk>/preview/', views.pr_document_preview,      name='pr_document_preview'),
    path('template/admin/download/',   views.admin_template_download,  name='admin_template_download'),

    # Member
    path('my/',                        views.my_prs,                   name='my_prs'),
    path('my/<int:pk>/',               views.pr_fill,                  name='pr_fill'),
    path('template/member/download/',  views.member_template_download, name='member_template_download'),

    # Tracking Master
    path('tracking-master/',           views.tracking_master,          name='tracking_master'),

    # User Master
    path('users/',                     views.user_master,              name='user_master'),
    path('users/template/download/',   views.user_template_download,   name='user_template_download'),
]
