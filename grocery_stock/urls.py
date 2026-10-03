"""
Grocery Stock & Cost Management — URL configuration
─────────────────────────────────────────────────────────────────────────────
Mounted by core/urls.py at /grocery/, so the API sits under /grocery/api/v1/
and the web pages under /grocery/ itself. Versioning the API path means the
Flutter client can be pinned to v1 while a future v2 is added alongside it.

The master page route is registered last so it cannot shadow anything above it.
"""
from django.urls import include, path
from rest_framework.routers import DefaultRouter

from . import views, views_pages, views_reports, views_txn

app_name = 'grocery_stock'

router = DefaultRouter()

# ── Masters ─────────────────────────────────────────────────────────────────
router.register(r'categories', views.GroceryCategoryViewSet, basename='category')
router.register(r'brands', views.GroceryBrandViewSet, basename='brand')
router.register(r'units', views.GroceryUnitViewSet, basename='unit')
router.register(r'suppliers', views.GrocerySupplierViewSet, basename='supplier')
router.register(r'warehouses', views.GroceryWarehouseViewSet, basename='warehouse')
router.register(r'locations', views.GroceryLocationViewSet, basename='location')
router.register(r'departments', views.GroceryDepartmentViewSet, basename='department')
router.register(r'items', views.GroceryItemViewSet, basename='item')

# ── Transactions (Phase 2) ──────────────────────────────────────────────────
router.register(r'goods-receipts', views_txn.GoodsReceiptViewSet, basename='goodsreceipt')
router.register(r'stock-issues', views_txn.StockIssueViewSet, basename='stockissue')
router.register(r'adjustments', views_txn.StockAdjustmentViewSet, basename='adjustment')

# Derived, read-only: these change only by posting a document.
router.register(r'stock', views_txn.StockViewSet, basename='stock')
router.register(r'ledger', views_txn.StockLedgerViewSet, basename='ledger')
router.register(r'batches', views_txn.StockBatchViewSet, basename='batch')
router.register(r'valuation', views_txn.ItemValuationViewSet, basename='valuation')

# ── Reporting (Phase 3) ─────────────────────────────────────────────────────
router.register(r'consumption', views_reports.ConsumptionReportViewSet,
                basename='consumption')

urlpatterns = [
    # Dashboard is a single aggregate endpoint, not a resource, so it sits
    # beside the router rather than being registered on it.
    path('api/v1/dashboard/', views_reports.GroceryDashboardView.as_view(),
         name='dashboard_api'),

    # JSON API — shared by the web pages here and, later, the Flutter client.
    path('api/v1/', include(router.urls)),

    # Web pages. The '<slug>/new/' route is declared before '<slug>/<pk>/' so
    # "new" is never captured as a primary key.
    path('', views_pages.home, name='home'),
    path('dashboard/', views_pages.dashboard, name='dashboard'),
    path('reports/', views_pages.reports_hub, name='reports'),
    path('master/<slug:slug>/', views_pages.master, name='master'),
    path('report/<slug:slug>/', views_pages.report, name='report'),
    path('doc/<slug:slug>/', views_pages.document_list, name='document_list'),
    path('doc/<slug:slug>/new/', views_pages.document_form, name='document_new'),
    path('doc/<slug:slug>/<int:pk>/', views_pages.document_form, name='document_detail'),
]
