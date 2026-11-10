from django.urls import path

from . import views

app_name = "frontdesk"
urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("new-lead/", views.new_lead, name="new_lead"),
    path("leads/<int:pk>/assign/", views.assign_lead, name="assign_lead"),
    path("leads/<int:pk>/suggest/", views.suggest_agent, name="suggest_agent"),
    path("leads/<int:pk>/remind/", views.remind_agent, name="remind_agent"),
    path("calendar/", views.calendar, name="calendar"),
    path("cards/", views.CardList.as_view(), name="cards"),
    path("cards/new/", views.CardCreate.as_view(), name="card_new"),
    path("cards/<int:pk>/edit/", views.CardUpdate.as_view(), name="card_edit"),
    path("cards/<int:pk>/delete/", views.card_delete, name="card_delete"),
    path("manual/", views.manual, name="manual"),
    path("queues/", views.QueueScreen.as_view(), name="queue"),
    path("queues/<int:pk>/assign/", views.queue_assign, name="queue_assign"),
    path("movement/", views.movement_board, name="movement"),
    path("movement/log/", views.MovementHistory.as_view(), name="movement_history"),
    path("movement/check-out/", views.checkout, name="checkout"),
    path("movement/<int:pk>/", views.movement_detail, name="movement_detail"),
    path("report/", views.report, name="report"),
    path("visitors/", views.visitors, name="visitors"),
    path("visitors/in/", views.visitor_in, name="visitor_in"),
    path("visitors/<int:pk>/out/", views.visitor_out, name="visitor_out"),
    path("appointments/add/", views.appointment_add, name="appointment_add"),
    path("appointments/<int:pk>/update/", views.appointment_update, name="appointment_update"),
    path("appointments/<int:pk>/status/", views.appointment_status, name="appointment_status"),
    path("equipment/", views.equipment_action, name="equipment_action"),
    path("shift-note/", views.shift_note, name="shift_note"),
]
