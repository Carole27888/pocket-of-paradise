from django.urls import path

from . import views

app_name = "schedule"
urlpatterns = [
    path("", views.calendar_view, name="calendar"),
    path("event/add/", views.event_add, name="event_add"),
    path("event/book-ceo/", views.book_ceo, name="book_ceo"),
    path("event/<int:pk>/", views.event_detail, name="event"),
    path("event/<int:pk>/action/", views.event_action, name="event_action"),
]
