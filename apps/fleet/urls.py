from django.urls import path

from . import views

app_name = "fleet"
urlpatterns = [
    path("", views.VehicleList.as_view(), name="list"),
    path("new/", views.VehicleCreate.as_view(), name="create"),
    path("<int:pk>/", views.VehicleDetail.as_view(), name="detail"),
    path("<int:pk>/edit/", views.VehicleUpdate.as_view(), name="edit"),
    path("<int:pk>/fuel/", views.add_fuel, name="add_fuel"),
    path("<int:pk>/maintenance/", views.add_maintenance, name="add_maintenance"),
    path("<int:pk>/status/", views.set_status, name="set_status"),
]
