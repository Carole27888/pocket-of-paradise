from django.urls import path

from . import views

app_name = "deals"
urlpatterns = [
    path("", views.overview, name="overview"),
    path("kyc/", views.kyc_list, name="kyc"),
    path("kyc/client/<int:client_id>/", views.kyc_submit, name="kyc_submit"),
    path("kyc/<int:pk>/decide/", views.kyc_decide, name="kyc_decide"),
    path("signatures/", views.signatures, name="signatures"),
    path("signatures/request/<str:kind>/<int:pk>/", views.signature_request, name="signature_request"),
    path("plan/<int:booking_id>/", views.plan, name="plan"),
    path("budgets/", views.budgets, name="budgets"),
]
