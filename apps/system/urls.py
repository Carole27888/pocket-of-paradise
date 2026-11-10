from django.urls import path

from . import views

app_name = "system"
urlpatterns = [
    path("roles/", views.roles_board, name="roles"),
    path("roles/move/", views.roles_move, name="roles_move"),
    path("logs/", views.SystemLogs.as_view(), name="logs"),
    path("keys/", views.credentials, name="credentials"),
    path("keys/<slug:provider>/save/", views.credential_save, name="credential_save"),
    path("keys/<slug:provider>/test/", views.credential_test, name="credential_test"),
    path("keys/<slug:provider>/delete/", views.credential_delete, name="credential_delete"),
    path("import/", views.imports_index, name="imports"),
    path("import/template/<slug:kind>/", views.import_template, name="import_template"),
    path("import/<int:pk>/", views.import_batch, name="import_batch"),
    path("import/<int:pk>/action/", views.import_action, name="import_action"),
    path("todo/", views.todo, name="todo"),
    path("todo/add/", views.todo_add, name="todo_add"),
    path("plan/add/", views.plan_add, name="plan_add"),
    path("plan/<int:pk>/edit/", views.plan_edit, name="plan_edit"),
    path("plan/<int:pk>/mark/", views.plan_mark, name="plan_mark"),
    path("plan/<int:pk>/delete/", views.plan_delete, name="plan_delete"),
    path("team-plans/", views.team_plans, name="team_plans"),
    path("todo/<int:pk>/", views.todo_action, name="todo_action"),
]
