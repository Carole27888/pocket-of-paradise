from django.urls import include, path
from rest_framework.authtoken.views import obtain_auth_token
from rest_framework.routers import DefaultRouter
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView

from . import views

router = DefaultRouter()
router.register("destinations", views.DestinationViewSet, basename="destination")
router.register("packages", views.PackageViewSet, basename="package")
router.register("enquiries", views.EnquiryViewSet, basename="enquiry")
router.register("movement", views.MovementViewSet, basename="movement")
router.register("departures", views.DepartureViewSet, basename="departure")
router.register("bookings", views.BookingViewSet, basename="booking")
router.register("guests", views.GuestViewSet, basename="guest")

urlpatterns = [
    path("auth/token/", obtain_auth_token, name="api_token"),
    path("auth/jwt/", TokenObtainPairView.as_view(), name="jwt_obtain"),
    path("auth/jwt/refresh/", TokenRefreshView.as_view(), name="jwt_refresh"),
    path("me/", views.MeView.as_view(), name="me"),
    path("", include(router.urls)),
]
