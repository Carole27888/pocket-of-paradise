from django.contrib import admin
from django.urls import include, path, re_path

from apps.core import views as core_views
from apps.compliance import views as compliance_views
from apps.catalog import public as catalog_public
from apps.dealdesk import views as deal_views
from apps.ict import views as ict_views
from apps.marketing import views as marketing_views
from apps.sales import cards as sales_cards
from apps.sales import views as sales_views

admin.site.site_header = "Pocket of Paradise Travel Company · Admin"
admin.site.site_title = "Pocket of Paradise Admin"

urlpatterns = [
    path("", include("apps.pwa.urls")),
    path("", include("apps.core.urls")),
    path("accounts/", include("apps.accounts.urls")),
    path("catalog/", include("apps.catalog.urls")),
    path("clients/", include("apps.contacts.urls")),
    path("enquiries/", include("apps.leads.urls")),
    path("fleet/", include("apps.fleet.urls")),
    path("frontdesk/", include("apps.frontdesk.urls")),
    path("sales/", include("apps.sales.urls")),
    path("trips/", include("apps.trips.urls")),
    path("finance/", include("apps.finance.urls")),
    path("marketing/", include("apps.marketing.urls")),
    path("system/", include("apps.system.urls")),
    path("schedule/", include("apps.schedule.urls")),
    path("hr/", include("apps.hr.urls")),
    path("coach/", include("apps.coach.urls")),
    path("insights/", include("apps.insights.urls")),
    path("tasks/", include("apps.tasks.urls")),
    path("mail/", include("apps.mailbox.urls")),
    path("help/", include("apps.manuals.urls")),
    path("reports/", include("apps.reports.urls")),
    path("ict/", include("apps.ict.urls")),
    path("assets/", include("apps.assets.urls")),
    path("deals/", include("apps.dealdesk.urls")),
    path("trip/<str:token>/", sales_views.trip_public, name="public_trip"),
    path("my-trip/<str:token>/", sales_views.trip_portal, name="public_trip_portal"),
    path("my-trip/<str:token>/calendar.ics", sales_views.trip_portal_calendar, name="public_trip_calendar"),
    path("my-trip/<str:token>/feedback/", sales_views.trip_portal_feedback, name="public_trip_feedback"),
    path("trip/<str:token>/proposal/", sales_views.trip_proposal, name="public_trip_proposal"),
    path("trip/<str:token>/pdf/", sales_views.trip_pdf, name="public_trip_pdf"),
    path("trip/<str:token>/f/<str:name>/", sales_views.trip_file, name="public_trip_file"),
    path("trip/<str:token>/respond/", sales_views.trip_respond, name="public_trip_respond"),
    path("trip/<str:token>/img/<int:day_id>/", sales_views.trip_image, name="public_trip_image"),
    path("trip/<str:token>/hotel/<int:hotel_id>/", sales_views.trip_hotel_image, name="public_trip_hotel_image"),
    path("card/<str:token>/", sales_cards.public_card, name="public_card"),
    path("card/<str:token>/contact.vcf", sales_cards.public_card_vcard, name="public_card_vcard"),
    path("card/<str:token>/plan/", sales_cards.public_card_plan, name="public_card_plan"),
    path("card/<str:token>/thanks/", sales_cards.public_card_thanks, name="public_card_thanks"),
    path("card/<str:token>/qr.png", sales_cards.public_card_qr, name="public_card_qr"),
    path("card/<str:token>/card.png", sales_cards.public_card_image, name="public_card_image"),
    path("webhooks/lead/", ict_views.website_hook, name="webhook_lead"),
    path("webhooks/meta/", ict_views.meta_hook, name="webhook_meta"),
    path("sign/<str:token>/", deal_views.public_sign, name="public_sign"),
    path("compliance/", include("apps.compliance.urls")),
    path("explore/", catalog_public.home, name="public_catalogue"),
    path("explore/<slug:slug>/", catalog_public.detail, name="public_package"),
    path("sitemap.xml", catalog_public.sitemap),
    path("robots.txt", catalog_public.robots),
    path("privacy/", compliance_views.privacy_notice, name="public_privacy"),
    path("production/", include("apps.marketing.production_urls")),
    path("capture/", marketing_views.capture, name="public_capture"),
    path("l/<slug:slug>/", marketing_views.track_redirect, name="track_link"),
    path("suppliers/", include("apps.suppliers.urls")),
    path("staff/", include("apps.people.urls")),
    path("notifications/", include("apps.notifications.urls")),
    path("api/v1/", include("apps.api.urls")),
    path("django-admin/", admin.site.urls),
    re_path(r"^media/(?P<path>.+)$", core_views.protected_media, name="protected_media"),
]

from apps.manuals.module_views import urlpatterns_for_modules  # noqa: E402

urlpatterns += urlpatterns_for_modules()

handler400 = "apps.core.views.error_400"
handler403 = "apps.core.views.error_403"
handler404 = "apps.core.views.error_404"
handler500 = "apps.core.views.error_500"
