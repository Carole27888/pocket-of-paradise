from django.urls import path

from . import library, views

app_name = "catalog"
urlpatterns = [
    path("destinations/", views.DestinationList.as_view(), name="destinations"),
    path("destinations/new/", views.DestinationCreate.as_view(), name="destination_create"),
    path("destinations/<slug:slug>/", views.DestinationDetail.as_view(), name="destination_detail"),
    path("destinations/<slug:slug>/edit/", views.DestinationUpdate.as_view(), name="destination_edit"),
    path("packages/", views.PackageList.as_view(), name="packages"),
    path("packages/new/", views.PackageCreate.as_view(), name="package_create"),
    path("packages/<slug:slug>/", views.PackageDetail.as_view(), name="package_detail"),
    path("packages/<slug:slug>/edit/", views.PackageUpdate.as_view(), name="package_edit"),
    path("packages/<slug:slug>/itinerary/", views.package_itinerary, name="package_itinerary"),
    path("accommodations/", views.AccommodationList.as_view(), name="accommodations"),
    path("accommodations/new/", views.AccommodationCreate.as_view(), name="accommodation_create"),
    path("accommodations/<int:pk>/", views.AccommodationDetail.as_view(), name="accommodation_detail"),
    path("accommodations/<int:pk>/edit/", views.AccommodationUpdate.as_view(), name="accommodation_edit"),
    path("accommodations/<int:pk>/rates/", views.accommodation_rates, name="accommodation_rates"),
    path("library/", library.library, name="library"),
    path("library/search/", library.photo_search, name="photo_search"),
    path("library/<slug:slug>/", library.photo_pack, name="photo_pack"),
    path("library/<slug:slug>/bulk/", library.photos_bulk, name="photos_bulk"),
    path("library/<slug:slug>/delete/", library.pack_delete, name="pack_delete"),
]
