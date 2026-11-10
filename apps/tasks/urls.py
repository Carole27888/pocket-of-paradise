from django.urls import path

from . import views

app_name = "tasks"
urlpatterns = [
    path("", views.task_list, name="list"),
    path("new/", views.task_new, name="new"),
    path("<int:pk>/done/", views.task_done, name="done"),
]
