from django.urls import path

from . import views

app_name = "insights"
urlpatterns = [
    path("", views.command_centre, name="centre"),
    path("ask/", views.ask, name="ask"),
    path("sales/", views.sales_insights, name="sales"),
    path("research/", views.research, name="research"),
    path("care/", views.customer_care, name="care"),
]
