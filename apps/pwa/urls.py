from django.urls import path

from . import views

urlpatterns = [
    path("manifest.webmanifest", views.manifest, name="pwa_manifest"),
    path("sw.js", views.service_worker, name="pwa_sw"),
    path("offline/", views.offline, name="pwa_offline"),
    path("healthz/", views.health, name="healthz"),
]
