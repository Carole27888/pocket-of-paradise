from django.urls import path

from apps.schedule import views as schedule_views
from apps.system import views as system_views

from . import leadlog, views, wa_views

app_name = "ict"
urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("lead-log/", leadlog.LeadLog.as_view(), name="lead_log"),
    path("chats/", views.chats, name="chats"),
    path("chats/<int:pk>/", views.chat, name="chat"),
    path("webhooks/", views.webhooks_page, name="webhooks"),
    path("broadcasts/", views.broadcasts, name="broadcasts"),
    path("broadcasts/new/", views.broadcast_form, name="broadcast_new"),
    path("broadcasts/preview/", views.broadcast_preview, name="broadcast_preview"),
    path("broadcasts/<int:pk>/", views.broadcast_detail, name="broadcast"),
    path("broadcasts/<int:pk>/edit/", views.broadcast_form, name="broadcast_edit"),
    path("unsubscribe/<str:token>/", views.unsubscribe, name="unsubscribe"),
    path("backups/", views.backups_page, name="backups"),
    path("backups/<int:pk>/download/", views.backup_download, name="backup_download"),
    path("analysis/", views.analysis, name="analysis"),
    path("training/", views.training, name="training"),
    path("training/try/", views.training_try, name="training_try"),
    path("training/seed/", views.training_seed, name="training_seed"),
    path("training/<int:pk>/action/", views.training_action, name="training_action"),
    path("whatsapp/", wa_views.whatsapp_page, name="whatsapp"),
    path("whatsapp/templates/new/", wa_views.template_form, name="wa_template_new"),
    path("whatsapp/templates/<int:pk>/", wa_views.template_form, name="wa_template_edit"),
    path("whatsapp/templates/<int:pk>/action/", wa_views.template_action, name="wa_template_action"),
    path("whatsapp/messages/", wa_views.message_log, name="message_log"),
    path("ai/", wa_views.ai_page, name="ai"),
    path("settings/", views.settings_view, name="settings"),
    path("settings/bot-toggle/", views.bot_toggle, name="bot_toggle"),
    path("settings/routing-toggle/", views.routing_toggle, name="routing_toggle"),
    path("settings/ai/", views.ai_choice, name="ai_choice"),
    path("settings/keys/<slug:provider>/save/", system_views.credential_save, name="credential_save"),
    path("settings/keys/<slug:provider>/test/", system_views.credential_test, name="credential_test"),
    path("settings/keys/<slug:provider>/delete/", system_views.credential_delete, name="credential_delete"),
    path("import/", system_views.imports_index, name="imports"),
    path("import/template/<slug:kind>/", system_views.import_template, name="import_template"),
    path("import/<int:pk>/", system_views.import_batch, name="import_batch"),
    path("import/<int:pk>/action/", system_views.import_action, name="import_action"),
    path("work-log/", views.work_log, name="work_log"),
    path("work-log/<int:pk>/delete/", views.work_log_delete, name="work_log_delete"),
    path("calendar/", schedule_views.calendar_view, name="calendar"),
    path("assistant/", views.assistant_ask, name="assistant"),
    path("webhooks/whatsapp/", views.whatsapp_webhook, name="whatsapp_webhook"),
]
