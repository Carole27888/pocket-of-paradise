"""'Your day' chips on the main menu: a few live numbers for the modules the signed-in person actually uses."""
from datetime import timedelta

from django.urls import reverse
from django.utils import timezone

from .modules import user_can_access_module

ICONS = {"Follow-ups overdue": "clock", "Follow-ups today": "calendar", "My open leads": "trending-up", "Leads waiting for an agent": "user-plus",
         "Visitors in the building": "users", "Appointments today": "calendar", "Overdue invoices": "file-text", "Bills due this week": "wallet",
         "Departures this week": "route", "Posts scheduled this week": "calendar", "Posts awaiting approval": "check",
         "Leave waiting for approval": "briefcase", "WhatsApp chats needing a person": "whatsapp", "Unread notifications": "bell", "My to-dos": "check-square"}


def _safe(fn):
    try:
        return fn()
    except Exception:  # a broken widget must never break the home page
        return None


def widgets_for(user):
    now, today = timezone.now(), timezone.localdate()
    out = []

    def add(label, value, url, tone=None):
        if value is None:
            return
        out.append({"label": label, "value": value, "url": url, "icon": ICONS.get(label, "activity"), "alert": tone == "alert" and bool(value)})

    emp = getattr(user, "employee", None) if hasattr(user, "employee") else None
    if user_can_access_module(user, "sales"):
        from apps.sales.models import FollowUp

        fu = FollowUp.objects.filter(done_at__isnull=True)
        if not user.sees_all_sales and emp:
            fu = fu.filter(agent=emp)
        add("Follow-ups overdue", _safe(lambda: fu.filter(due_at__lt=now).count()), reverse("sales:followups"), "alert")
        add("Follow-ups today", _safe(lambda: fu.filter(due_at__date=today).count()), reverse("sales:followups"))
        from apps.leads.views import visible_enquiries

        add("My open leads", _safe(lambda: visible_enquiries(user).filter(stage__in=["enquiry", "quoted", "hot", "itinerary", "deposit"]).count()), reverse("sales:board"))
    if user_can_access_module(user, "frontdesk"):
        from apps.frontdesk import services as fd

        add("Leads waiting for an agent", _safe(lambda: fd.filter_state(fd.desk_enquiries(), "waiting").count()), reverse("frontdesk:dashboard"), "alert")
        from apps.frontdesk.models import Appointment, Visitor

        add("Visitors in the building", _safe(lambda: Visitor.objects.filter(time_out__isnull=True).count()), reverse("frontdesk:visitors"))
        add("Appointments today", _safe(lambda: Appointment.objects.filter(start__date=today).exclude(status="cancelled").count()), reverse("frontdesk:visitors"))
    if user_can_access_module(user, "finance"):
        from apps.finance.models import Invoice

        add("Overdue invoices", _safe(lambda: Invoice.objects.filter(status__in=["sent", "part_paid"], due_date__lt=today).count()), reverse("finance:dashboard"), "alert")
        from apps.suppliers.models import Bill

        add("Bills due this week", _safe(lambda: Bill.objects.exclude(status="paid").filter(due_date__lte=today + timedelta(days=7)).count()), reverse("suppliers:bills"))
    if user_can_access_module(user, "trips"):
        from apps.trips.models import Departure

        add("Departures this week", _safe(lambda: Departure.objects.filter(start_date__range=(today, today + timedelta(days=7))).exclude(stage="cancelled").count()), "/trips/")
    if user_can_access_module(user, "marketing"):
        from apps.marketing.models import Post

        add("Posts scheduled this week", _safe(lambda: Post.objects.filter(status="scheduled", scheduled_at__date__range=(today, today + timedelta(days=7))).count()), reverse("marketing:content"))
        add("Posts awaiting approval", _safe(lambda: Post.objects.filter(status="needs_approval").count()), reverse("marketing:content"), "alert")
    if user_can_access_module(user, "hr"):
        from apps.hr.models import LeaveRequest

        add("Leave waiting for approval", _safe(lambda: LeaveRequest.objects.filter(status="pending").count()), reverse("hr:approvals"), "alert")
    if user_can_access_module(user, "ict"):
        from apps.ict.models import BotConversation

        add("WhatsApp chats needing a person", _safe(lambda: BotConversation.objects.filter(status="human").count()), reverse("ict:chats"), "alert")
    from apps.notifications.models import Notification
    from apps.system.models import TodoItem

    add("Unread notifications", _safe(lambda: Notification.objects.filter(user=user, read_at__isnull=True).count()), reverse("notifications:list"))
    add("My to-dos", _safe(lambda: TodoItem.objects.filter(user=user, done_at__isnull=True).count()), reverse("system:todo"))
    return out[:8]




def setup_checklist(user):
    """A "finish setting up" list for the people who run the system: what is still empty that clients or staff will notice. None once all is done."""
    from apps.accounts import roles as R

    if not (user.is_superuser or user.has_role(R.ADMIN, R.COO)):
        return None
    from apps.catalog.models import Accommodation, Destination, LibraryPhoto, TourPackage
    from apps.ict.models import KnowledgeEntry
    from apps.system.models import IntegrationCredential

    from .models import CompanySetting

    c = CompanySetting.load()
    steps = [
        ("Company profile", bool(c.phone and c.email), "core:company", "Add your phone, e-mail and address: they appear on every quotation and itinerary."),
        ("Bank and Paybill details", bool(c.bank_details.strip() and c.paybill), "core:company", "Clients are told where to pay on their itinerary and trip page."),
        ("Destinations", Destination.objects.alive().exists(), "catalog:destination_create", "The places you sell."),
        ("Lodges with rates", Accommodation.objects.alive().filter(rates__isnull=False).exists(), "catalog:accommodations", "Rates fill in the price when an agent picks a lodge."),
        ("Published packages", TourPackage.objects.alive().filter(status="published").exists(), "catalog:package_create", "Ready-made trips agents can start from."),
        ("Photo library", LibraryPhoto.objects.exists(), "catalog:library", "Photos for itineraries and PDFs."),
        ("AI and messaging keys", IntegrationCredential.objects.exists(), "system:credentials", "WhatsApp, SMS, e-mail and AI keys."),
        ("Teach the WhatsApp bot", KnowledgeEntry.objects.exists(), "ict:training", "Answers the bot gives word for word."),
    ]
    from django.urls import NoReverseMatch, reverse

    rows = []
    for label, done, url_name, hint in steps:
        try:
            url = reverse(url_name)
        except NoReverseMatch:
            url = "#"
        rows.append({"label": label, "done": done, "url": url, "hint": hint})
    left = [r for r in rows if not r["done"]]
    if not left:
        return None
    return {"rows": rows, "done": len(rows) - len(left), "total": len(rows), "percent": round(100 * (len(rows) - len(left)) / len(rows))}
