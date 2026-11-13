from django.urls import path

from . import competitor_views, views

app_name = "insights"
urlpatterns = [
    path("", views.command_centre, name="centre"),
    path("ask/", views.ask, name="ask"),
    path("sales/", views.sales_insights, name="sales"),
    path("research/", views.research, name="research"),
    path("care/", views.customer_care, name="care"),
    path("competitors/", competitor_views.competitors, name="competitors"),
    path("competitors/new/", competitor_views.competitor_form, name="competitor_new"),
    path("competitors/<int:pk>/", competitor_views.competitor_detail, name="competitor"),
    path("competitors/<int:pk>/edit/", competitor_views.competitor_form, name="competitor_edit"),
    path("competitors/searches/<int:pk>/delete/", competitor_views.search_delete, name="competitor_search_delete"),
]
