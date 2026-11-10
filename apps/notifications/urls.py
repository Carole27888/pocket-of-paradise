from django.urls import path

from . import views

app_name = "notifications"
urlpatterns = [
    path("", views.dropdown, name="dropdown"),
    path("all/", views.page, name="list"),
    path("live/", views.live, name="live"),
    path("board/", views.board, name="board"),
    path("board/new/", views.board_form, name="board_new"),
    path("board/<int:pk>/edit/", views.board_form, name="board_edit"),
    path("board/<int:pk>/delete/", views.board_delete, name="board_delete"),
    path("<int:pk>/open/", views.open_notification, name="open"),
    path("read-all/", views.mark_all_read, name="read_all"),
    path("push/subscribe/", views.push_subscribe, name="push_subscribe"),
    path("push/unsubscribe/", views.push_unsubscribe, name="push_unsubscribe"),
]
