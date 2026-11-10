from django.urls import path

from . import views

app_name = "trips"
urlpatterns = [
    path("", views.overview, name="overview"),
    path("board/", views.board, name="board"),
    path("departures/", views.DepartureList.as_view(), name="list"),
    path("departures/new/", views.DepartureCreate.as_view(), name="create"),
    path("departures/<int:pk>/", views.detail, name="detail"),
    path("departures/<int:pk>/edit/", views.DepartureUpdate.as_view(), name="edit"),
    path("departures/<int:pk>/assign/", views.assign, name="assign"),
    path("departures/<int:pk>/checklist/", views.checklist, name="checklist"),
    path("departures/<int:pk>/advance/", views.advance, name="advance"),
    path("departures/<int:pk>/attach/", views.attach, name="attach"),
    path("departures/<int:pk>/manifest/", views.manifest, name="manifest"),
    path("departures/<int:pk>/manifest/check-all/", views.manifest_check_all, name="manifest_check_all"),
    path("departures/<int:pk>/manifest/add/", views.manifest_edit, name="manifest_add"),
    path("departures/<int:pk>/manifest/<int:entry_id>/", views.manifest_edit, name="manifest_edit"),
    path("departures/<int:pk>/manifest/<int:entry_id>/check/", views.manifest_check, name="manifest_check"),
    path("departures/<int:pk>/incident/", views.incident_new, name="incident_new"),
    path("departures/<int:pk>/expense/", views.expense_new, name="expense_new"),
    path("incidents/", views.IncidentList.as_view(), name="incidents"),
    path("incidents/<int:pk>/", views.incident, name="incident"),
    path("reports/", views.reports, name="reports"),
]
