from django.urls import path

from . import production as views

app_name = "production"
urlpatterns = [
    path("", views.board, name="board"),
    path("overview/", views.overview, name="overview"),
    path("reports/", views.reports, name="reports"),
    path("shoots/", views.ShootList.as_view(), name="list"),
    path("shoots/new/", views.ShootCreate.as_view(), name="create"),
    path("shoots/<int:pk>/", views.detail, name="detail"),
    path("shoots/<int:pk>/edit/", views.ShootUpdate.as_view(), name="edit"),
    path("shoots/<int:pk>/advance/", views.advance, name="advance"),
    path("shoots/<int:pk>/back/", views.retreat, name="retreat"),
]
