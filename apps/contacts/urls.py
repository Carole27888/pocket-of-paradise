from django.urls import path

from . import views

app_name = "contacts"
urlpatterns = [
    path("", views.ClientList.as_view(), name="list"),
    path("new/", views.ClientCreate.as_view(), name="create"),
    path("<int:pk>/", views.ClientDetail.as_view(), name="detail"),
    path("<int:pk>/edit/", views.ClientUpdate.as_view(), name="edit"),
]
