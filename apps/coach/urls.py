from django.urls import path

from . import views

app_name = "coach"
urlpatterns = [
    path("", views.inbox, name="inbox"),
    path("<int:pk>/reply/", views.reply, name="reply"),
    path("team/", views.team_performance, name="team"),
    path("settings/", views.settings_view, name="settings"),
]
