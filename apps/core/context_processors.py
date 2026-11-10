from django.conf import settings

from .modules import BY_KEY, modules_for, user_can_access_module


def app_shell(request):
    ctx = {
        "COMPANY_NAME": settings.COMPANY_NAME,
        "COMPANY_TAGLINE": settings.COMPANY_TAGLINE,
        "PWA_VERSION": settings.PWA_VERSION,
        "VAPID_PUBLIC_KEY": settings.VAPID_PUBLIC_KEY,
    }
    user = getattr(request, "user", None)
    if not user or not user.is_authenticated:
        return ctx
    match = getattr(request, "resolver_match", None)
    view_name = match.view_name if match else ""
    key = getattr(request, "current_module", None) or _guess_module(view_name)
    module = BY_KEY.get(key) or BY_KEY["operations"]
    if not user_can_access_module(user, module.key):      # e.g. the main menu for a role with no Operations access
        mine = modules_for(user)
        module = mine[0] if mine else BY_KEY["me"]
    nav = []
    active_found = False
    for item in module.nav_for(user):
        if item.args:
            active = request.path == item.url and not active_found
        else:
            active = any(view_name.startswith(p) for p in item.match) and not active_found
        active_found = active_found or active
        kids = [{"item": c, "active": active and any(view_name.startswith(p) for p in c.match)} for c in item.children if not c.roles or user.has_role(*c.roles)]
        nav.append({"item": item, "active": active, "children": kids})
    on_home = view_name == "core:home"
    # My Space is not a drop-down: its screens sit in the menu itself, right under Main menu
    my_space, found = [], False
    me = BY_KEY.get("me")
    for item in (me.nav_for(user) if me else []):
        active = (request.path == item.url if item.args else any(view_name.startswith(p) for p in item.match)) and not found
        found = found or active
        my_space.append({"item": item, "active": active})
    sidebar_modules = [{"module": m, "url": m.url_for(user), "current": m.key == module.key and not on_home}
                       for m in modules_for(user) if m.available and m.key != "me"]
    quick = _quick_add(user)
    try:
        from apps.system.planning import nudge as plan_nudge

        plan_due = plan_nudge(user)
    except Exception:
        plan_due = None
    from apps.notifications.models import Notification

    ctx.update(
        {
            "current_module": module,
            "nav_items": nav,
            "bottom_items": [n for n in nav if n["item"].bottom][:4],
            "user_modules": modules_for(user),
            "sidebar_modules": sidebar_modules,
            "my_space": my_space,
            "plan_due": plan_due,
            "on_home": on_home,
            "quick_add": quick,
            "unread_notifications": Notification.objects.filter(user=user, read_at__isnull=True).count(),
        }
    )
    return ctx


def _guess_module(view_name: str) -> str:
    if view_name.startswith(("core:system_status", "core:company", "core:security_settings", "accounts:user", "accounts:install")):
        return "admin"
    if view_name.startswith("frontdesk:"):
        return "frontdesk"
    if view_name.startswith("sales:"):
        return "sales"
    if view_name.startswith("trips:"):
        return "trips"
    if view_name.startswith("schedule:") or view_name.startswith("hr:my") or view_name.startswith("hr:cancel"):
        return "me"
    if view_name.startswith("hr:"):
        return "hr"
    if view_name.startswith("deals:budgets"):
        return "me"
    if view_name.startswith("deals:") or view_name.startswith("public_sign"):
        return "deals"
    if view_name.startswith(("tasks:", "mailbox:")):
        return "me"
    if view_name.startswith(("insights:",)):
        return "intelligence"
    if view_name.startswith("coach:"):
        return "sales"
    if view_name.startswith("reports:"):
        return "sales"
    if view_name.startswith("manuals:"):
        return "me"
    if view_name.startswith("ict:"):
        return "ict"
    if view_name.startswith("compliance:"):
        return "compliance"
    if view_name.startswith(("accounts:security", "accounts:profile")):
        return "me"
    if view_name.startswith("system:"):
        return "me" if view_name.startswith("system:todo") else "admin"
    if view_name.startswith("production:"):
        return "production"
    if view_name.startswith("marketing:"):
        return "marketing"
    if view_name.startswith("finance:") or view_name.startswith("assets:"):
        return "finance"
    if view_name.startswith("suppliers:"):
        return "suppliers"
    return "operations"


def _quick_add(user):
    """The "+" menu in the top bar: the things this person creates most, as links (some open as pop-ups)."""
    from django.urls import reverse

    out = []

    def add(label, name, icon, modal="", qs=""):
        try:
            url = reverse(name) + qs
        except Exception:
            return
        out.append({"label": label, "url": url, "icon": icon, "modal": modal})

    if user.can_run_desk:
        add("New lead", "frontdesk:new_lead", "user-plus")
    elif user.can_work_sales:
        add("New lead", "leads:create", "user-plus")
    if user.can_work_sales:
        add("Log a call", "sales:call_new", "phone", "Log a call")
        add("Build an itinerary", "sales:itinerary_new", "route")
    if user.can_manage_clients:
        add("New client", "contacts:create", "users", "New client")
    if user.can_run_desk:
        add("New business card", "frontdesk:card_new", "contact", "New business card")
    if user.can_manage_marketing:
        add("Schedule a post", "marketing:post_new", "calendar", "New post", "?from=calendar")
    add("Add to my to-do", "system:todo", "check-square")
    return out
