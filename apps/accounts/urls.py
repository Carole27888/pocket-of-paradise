from django.urls import path

from . import views

app_name = "accounts"
urlpatterns = [
    path("login/", views.login_view, name="login"),
    path("logout/", views.logout_view, name="logout"),
    path("users/", views.users, name="users"),
    path("users/new/", views.user_edit, name="user_create"),
    path("users/<int:pk>/", views.user_edit, name="user_edit"),
    path("install/", views.install, name="install"),
    path("two-factor/", views.two_factor_challenge, name="two_factor"),
    path("profile/", views.profile, name="profile"),
    path("security/", views.security, name="security"),
    path("users/<int:pk>/reset-2fa/", views.reset_two_factor, name="reset_2fa"),
]
