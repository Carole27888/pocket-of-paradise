from django.urls import path

from . import views

app_name = "core"
urlpatterns = [
    path("", views.home, name="home"),
    path("operations/", views.ops_dashboard, name="ops_dashboard"),
    path("search/", views.global_search, name="search"),
    path("system/status/", views.system_status, name="system_status"),
    path("system/jobs/<int:pk>/retry/", views.retry_job, name="retry_job"),
    path("system/jobs/run/", views.run_jobs_now, name="run_jobs_now"),
    path("system/company/", views.company, name="company"),
    path("system/security/", views.security_settings, name="security_settings"),
    path("operations/standard-terms/", views.standard_terms_page, name="standard_terms"),
]
