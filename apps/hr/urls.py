from django.urls import path

from . import views

app_name = "hr"
urlpatterns = [
    path("", views.overview, name="overview"),
    path("my/", views.my_leave, name="my"),
    path("my/<int:pk>/cancel/", views.cancel, name="cancel"),
    path("approvals/", views.approvals, name="approvals"),
    path("approvals/<int:pk>/", views.decide, name="decide"),
    path("clock/", views.clock_action, name="clock"),
    path("clock/status/", views.clock_status, name="clock_status"),
    path("attendance/me/", views.my_attendance, name="my_attendance"),
    path("attendance/", views.attendance, name="attendance"),
    path("settings/", views.settings_view, name="settings"),
]
