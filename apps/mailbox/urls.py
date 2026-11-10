from django.urls import path

from . import views

app_name = "mailbox"
urlpatterns = [
    path("", views.inbox, name="inbox"),
    path("setup/", views.setup, name="setup"),
    path("compose/", views.compose, name="compose"),
    path("sent/", views.sent, name="sent"),
    path("settings/", views.settings_view, name="settings"),
]
