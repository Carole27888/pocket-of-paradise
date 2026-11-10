from django.urls import path

from . import views

app_name = "suppliers"
urlpatterns = [
    path("", views.SupplierList.as_view(), name="list"),
    path("new/", views.SupplierCreate.as_view(), name="create"),
    path("<int:pk>/", views.supplier_detail, name="detail"),
    path("<int:pk>/edit/", views.SupplierUpdate.as_view(), name="edit"),
    path("bills/", views.BillList.as_view(), name="bills"),
    path("bills/new/", views.bill_new, name="bill_new"),
    path("bills/<int:pk>/", views.bill_detail, name="bill"),
    path("departures/", views.DepartureCosts.as_view(), name="departures"),
]
