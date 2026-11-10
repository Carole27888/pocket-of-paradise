from django.urls import path

from . import views

app_name = "compliance"
urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("requests/", views.RequestList.as_view(), name="requests"),
    path("requests/new/", views.RequestCreate.as_view(), name="request_new"),
    path("requests/<int:pk>/", views.request_detail, name="request"),
    path("requests/<int:pk>/action/", views.request_action, name="request_action"),
    path("breaches/", views.BreachList.as_view(), name="breaches"),
    path("breaches/new/", views.BreachCreate.as_view(), name="breach_new"),
    path("breaches/<int:pk>/", views.breach_detail, name="breach"),
    path("breaches/<int:pk>/edit/", views.BreachUpdate.as_view(), name="breach_edit"),
    path("retention/", views.retention, name="retention"),
]
