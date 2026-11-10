from django.urls import path

from . import views

app_name = "leads"
urlpatterns = [
    path("", views.EnquiryList.as_view(), name="list"),
    path("sources/", views.lead_sources, name="sources"),
    path("new/", views.enquiry_create, name="create"),
    path("returning-check/", views.returning_check, name="returning_check"),
    path("agents/load/", views.agent_load, name="agent_load"),
    path("bulk/stage/", views.enquiry_bulk_stage, name="bulk_stage"),
    path("<int:pk>/", views.enquiry_detail, name="detail"),
    path("<int:pk>/edit/", views.enquiry_edit, name="edit"),
    path("<int:pk>/stage/", views.enquiry_stage, name="stage"),
    path("<int:pk>/assign/", views.enquiry_assign, name="assign"),
    path("<int:pk>/draft/", views.enquiry_draft, name="draft"),
    path("<int:pk>/note/", views.enquiry_note, name="note"),
]
