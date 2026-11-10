from django.urls import path

from . import views

app_name = "manuals"
urlpatterns = [path("", views.index, name="index"), path("<slug:slug>/", views.manual, name="manual")]
