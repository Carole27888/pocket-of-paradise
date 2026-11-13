"""Module registry: drives the sidebar, module switcher, bottom navigation and access checks."""
from dataclasses import dataclass, field, replace

from django.urls import NoReverseMatch, reverse

from apps.accounts import roles as R


@dataclass
class NavItem:
    label: str
    url_name: str
    icon: str
    match: tuple = ()  # url-name prefixes that mark this item active
    bottom: bool = False
    bottom_label: str = ""
    roles: frozenset = frozenset()  # empty = every role that can open the module
    children: tuple = ()  # sub-items shown in a dropdown under this item
    section: str = ""  # a small heading shown above this item (and the ones after it) in the sidebar
    args: tuple = ()  # url arguments; an item with args is active only on its exact address

    @property
    def url(self):
        try:
            return reverse(self.url_name, args=self.args)
        except NoReverseMatch:
            return "#"


@dataclass
class Module:
    key: str
    name: str
    icon: str
    description: str
    roles: set
    phase: int
    nav: list = field(default_factory=list)
    available: bool = True

    def __post_init__(self):
        # an item without a section belongs to the one above it, so a group's heading still shows when its first item is hidden for someone
        cur, out = "", []
        for n in self.nav:
            cur = n.section or cur
            out.append(n if n.section == cur else replace(n, section=cur))
        self.nav = out

    @property
    def home_url(self):
        return self.nav[0].url if self.nav else "#"

    def nav_for(self, user):
        return [n for n in self.nav if not n.roles or user.has_role(*n.roles)]

    def url_for(self, user):
        """Where this module's tile should land for this person: the first screen they may actually open."""
        mine = self.nav_for(user)
        return mine[0].url if mine else self.home_url


ALL = set(R.ALL_ROLES)
# Sales people live in the Sales module only; every other role keeps Operations.
OPS_ROLES = ALL - R.OWN_MODULE_ONLY
MGR = frozenset(R.SALES_SEE_ALL)  # sales manager, admin, director

MODULES: list[Module] = [
    Module(
        "operations", "Operations", "compass",
        "Catalogue, clients, enquiries, fleet and staff shared by every team.",
        OPS_ROLES, 1,
        [
            NavItem("Operations Dashboard", "core:ops_dashboard", "grid", ("core:ops_dashboard",), True, "Home"),
            NavItem("Enquiries", "leads:list", "inbox", ("leads:",), True, "Enquiries"),
            NavItem("Clients", "contacts:list", "users", ("contacts:",), True, "Clients"),
            NavItem("Destinations", "catalog:destinations", "map-pin", ("catalog:destination",)),
            NavItem("Tour Packages", "catalog:packages", "map", ("catalog:package",)),
            NavItem("Accommodations", "catalog:accommodations", "bed", ("catalog:accommodation",)),
            NavItem("Photo Library", "catalog:library", "image", ("catalog:library", "catalog:photo")),
            NavItem("Fleet", "fleet:list", "truck", ("fleet:",), True, "Fleet"),
            NavItem("Staff & Guides", "people:list", "id-card", ("people:",)),
            NavItem("Standard Terms", "core:standard_terms", "file-text", ("core:standard_terms",), roles=frozenset({R.OPERATIONS, R.COO, R.ADMIN})),
            NavItem("Competitor Watch", "insights:competitors", "eye", ("insights:competitor",), roles=frozenset({R.OPERATIONS})),
        ],
    ),
    Module(
        "frontdesk", "Front Desk", "desk", "Reservations desk: WhatsApp, walk-ins, calls, DMs, trip movement.",
        {R.ADMIN, R.DIRECTOR, R.OPERATIONS, *R.FRONTDESK_ROLES}, 2,
        [
            NavItem("Front Desk Dashboard", "frontdesk:dashboard", "desk", ("frontdesk:dashboard",), True, "Desk"),
            NavItem("New Lead", "frontdesk:new_lead", "user-plus", ("frontdesk:new_lead",), True, "New", frozenset(R.DESK_OPERATORS)),
            NavItem("Leads Log", "frontdesk:queue", "list", ("frontdesk:queue",), True, "Leads"),
            NavItem("Visitors & Appointments", "frontdesk:visitors", "users", ("frontdesk:visitors", "frontdesk:appointment")),
            NavItem("Business Cards", "frontdesk:cards", "contact", ("frontdesk:card",)),
            NavItem("Weekend Roster", "frontdesk:weekend_roster", "calendar", ("frontdesk:weekend_roster",)),
            NavItem("Calendar", "frontdesk:calendar", "calendar", ("frontdesk:calendar",)),
            NavItem("Reports", "frontdesk:report", "bar-chart", ("frontdesk:report",), True, "Reports"),
            NavItem("User Manual", "frontdesk:manual", "file-text", ("frontdesk:manual",)),
        ],
    ),
    Module(
        "sales", "Sales", "trending-up", "Booking pipeline, assignment, calls, quotations and bookings.",
        {R.ADMIN, R.DIRECTOR, R.SALES_MANAGER, R.SALES_AGENT}, 3,
        [
            NavItem("Sales Dashboard", "sales:dashboard", "trending-up", ("sales:dashboard",), True, "Home"),
            NavItem("Pipeline Board", "sales:board", "columns", ("sales:board",), True, "Pipeline"),
            # ---- working leads
            NavItem("Leads", "leads:list", "inbox", ("leads:",), True, "Leads", section="Leads"),
            NavItem("Follow-ups", "sales:followups", "check-square", ("sales:followup",), True, "Tasks"),
            NavItem("Calls", "sales:calls", "phone", ("sales:call",)),
            NavItem("WhatsApp Chats", "ict:chats", "whatsapp", ("ict:chat",), roles=frozenset({R.SALES_MANAGER})),
            # ---- from itinerary to booking
            NavItem("Itineraries", "sales:itineraries", "route", ("sales:itinerar", "sales:template"), section="Selling", children=(
                NavItem("All itineraries", "sales:itineraries", "list", ("sales:itineraries", "sales:itinerary_detail", "sales:itinerary_edit", "sales:itinerary_print",
                                                                          "sales:itinerary_pdf", "sales:itinerary_quote", "sales:itinerary_send", "sales:itinerary_rate")),
                NavItem("Build an itinerary", "sales:itinerary_new", "plus", ("sales:itinerary_new",), roles=frozenset({R.ADMIN, R.COO, R.SALES_MANAGER, R.SALES_AGENT})),
                NavItem("Templates", "sales:templates", "copy", ("sales:template",)),
                NavItem("Photo Library", "catalog:library", "image", ("catalog:library", "catalog:photo")))),
            NavItem("Quotations", "sales:quotations", "file-text", ("sales:quotation",)),
            NavItem("Bookings", "sales:bookings", "briefcase", ("sales:booking",)),
            NavItem("Letters", "sales:letters", "mail", ("sales:letter",)),
            NavItem("Client KYC", "deals:kyc", "shield", ("deals:kyc",), roles=frozenset(R.SALES_ROLES)),          # managers find these in Deal Desk
            NavItem("E-Signatures", "deals:signatures", "edit", ("deals:signature",), roles=frozenset(R.SALES_ROLES)),
            # ---- the people and trips agents sell to (shared records; other roles open them from Operations)
            NavItem("Clients", "contacts:list", "users", ("contacts:",), section="Clients & packages"),
            NavItem("Packages", "catalog:packages", "map", ("catalog:package", "catalog:destination")),
            NavItem("Customer Care", "insights:care", "alert", ("insights:care",), roles=frozenset(R.SALES_ROLES)),
            NavItem("Client Feedback", "sales:feedback", "star", ("sales:feedback",), roles=MGR),
            # ---- the agent's own corner
            NavItem("My Performance", "sales:me", "target", ("sales:me",), roles=frozenset({R.SALES_AGENT}), section="My work"),
            NavItem("My QR Card", "sales:my_card", "id-card", ("sales:my_card",), roles=frozenset({R.SALES_AGENT, R.SALES_MANAGER})),
            NavItem("My Assistant", "coach:inbox", "bell", ("coach:inbox", "coach:reply"), roles=frozenset({R.SALES_AGENT, R.SALES_MANAGER})),
            # ---- managing the team
            NavItem("Assign Leads", "sales:assign_leads", "user-plus", ("sales:assign_leads",), roles=frozenset({R.SALES_MANAGER, R.COO, R.ADMIN, R.DIRECTOR}), section="Team & leads"),
            NavItem("Team", "sales:team", "id-card", ("sales:team",), roles=MGR),
            NavItem("Targets", "sales:targets", "target", ("sales:targets",), roles=MGR),
            NavItem("Agent Cards", "sales:cards", "id-card", ("sales:cards", "sales:card_detail"), roles=frozenset({R.ADMIN, R.DIRECTOR, R.COO, R.SALES_MANAGER})),
            NavItem("Team Performance", "coach:team", "bar-chart", ("coach:team",), roles=frozenset({R.SALES_MANAGER})),   # the others have it in Intelligence
            NavItem("Lead Routing", "sales:assignment", "shuffle", ("sales:assignment",), roles=MGR),
            NavItem("Double Allocation", "sales:double_allocation", "alert", ("sales:double_allocation",), roles=MGR),
            NavItem("Lead Sources", "leads:sources", "share", ("leads:sources",), roles=frozenset({R.COO, R.ADMIN, R.DIRECTOR, R.SALES_MANAGER})),
            # ---- numbers
            NavItem("Sales Reports", "sales:reports", "bar-chart", ("sales:reports",), roles=MGR, section="Reports"),
            NavItem("Sales Insights", "insights:sales", "trending-up", ("insights:sales",), roles=frozenset({R.SALES_MANAGER})),   # the others have it in Intelligence
            NavItem("Competitor Watch", "insights:competitors", "eye", ("insights:competitor",), roles=frozenset({R.SALES_MANAGER})),
            NavItem("All Reports", "reports:index", "bar-chart", ("reports:",)),
            # ---- settings
            NavItem("Automation", "sales:automation", "sparkles", ("sales:automation",), roles=MGR, section="Settings"),
            NavItem("Assistant Settings", "coach:settings", "settings", ("coach:settings",), roles=frozenset({R.SALES_MANAGER, R.COO, R.DIRECTOR, R.ADMIN})),
        ],
    ),
    Module(
        "trips", "Trip Readiness", "route", "Departures, manifests, guide and vehicle readiness, incidents.",
        {R.ADMIN, R.DIRECTOR, R.OPERATIONS, R.FRONTDESK_MANAGER, R.DISPATCHER, R.GUIDE}, 6,
        [
            NavItem("Progress Overview", "trips:overview", "grid", ("trips:overview",), True, "Home"),
            NavItem("Departures Board", "trips:board", "columns", ("trips:board",), True, "Board"),
            NavItem("All Departures", "trips:list", "calendar", ("trips:list", "trips:detail", "trips:manifest",
                                                              "trips:create", "trips:edit"), True, "Trips"),
            NavItem("Incidents", "trips:incidents", "alert", ("trips:incident",), True, "Incidents"),
            NavItem("Reports", "trips:reports", "bar-chart", ("trips:reports",),
                    roles=frozenset({R.ADMIN, R.DIRECTOR, R.OPERATIONS, R.FRONTDESK_MANAGER})),
        ],
    ),
    Module(
        "finance", "Finance", "wallet", "Invoicing, receipts, payables, KRA, payroll and statements.",
        {R.ADMIN, R.DIRECTOR, R.FINANCE}, 4,
        [
            NavItem("Finance Dashboard", "finance:dashboard", "wallet", ("finance:dashboard",), True, "Home"),
            NavItem("Sales & Revenue", "finance:revenue", "trending-up", ("finance:revenue",)),
            NavItem("Receivables & Debt", "finance:receivables", "alert", ("finance:receivables",), True, "Debtors"),
            NavItem("Invoicing", "finance:invoices", "file-text", ("finance:invoice",), True, "Invoices"),
            NavItem("Receipts & Collections", "finance:receipts", "check-square", ("finance:receipt", "finance:mpesa"), True, "Receipts"),
            NavItem("Bank Reconciliation", "finance:bank", "columns", ("finance:bank",)),
            NavItem("Supplier Payables", "finance:payables", "truck", ("finance:payables", "finance:bill", "finance:payment_run")),
            NavItem("Expenses & KRA", "finance:expenses", "shield", ("finance:expense", "finance:kra", "finance:tax")),
            NavItem("Payroll", "finance:payroll", "users", ("finance:payroll",)),
            NavItem("Agent Commission", "finance:commission", "trending-up", ("finance:commission",)),
            NavItem("Finance Calendar", "finance:calendar", "calendar", ("finance:calendar",)),
            NavItem("Financial Statements", "finance:statements", "columns", ("finance:statements",)),
            NavItem("Budgets", "finance:budgets", "target", ("finance:budgets",)),
            NavItem("Reports", "finance:reports", "bar-chart", ("finance:report",)),
            NavItem("Fixed Assets", "assets:register", "briefcase", ("assets:",)),
        ],
    ),
    Module(
        "suppliers", "Suppliers", "briefcase", "Lodges, camps, transporters, caterers and the bills we owe them.",
        {R.ADMIN, R.DIRECTOR, R.SUPPLIER_COORDINATOR, R.FINANCE, R.OPERATIONS}, 5,
        [
            NavItem("Suppliers", "suppliers:list", "briefcase", ("suppliers:list", "suppliers:detail", "suppliers:create", "suppliers:edit"), True, "Suppliers"),
            NavItem("Bills & Prepayments", "suppliers:bills", "file-text", ("suppliers:bill",), True, "Bills"),
            NavItem("Departure Costs", "suppliers:departures", "route", ("suppliers:departure",), True, "Trips"),
        ],
    ),
    Module(
        "marketing", "Marketing", "megaphone", "Channels, campaigns, content, inbox and analytics.",
        {R.ADMIN, R.DIRECTOR, R.MARKETING}, 5,
        [
            NavItem("Dashboard", "marketing:dashboard", "grid", ("marketing:dashboard",), True, "Home", section="Media"),
            NavItem("Social Media", "marketing:social", "share", ("marketing:social",), True, "Social"),
            NavItem("Leads", "marketing:enquiries", "inbox", ("marketing:enquiries", "marketing:lead_journey"), True, "Leads"),
            NavItem("Lead Quality", "marketing:lead_quality", "target", ("marketing:lead_quality",), True, "Quality"),
            NavItem("Campaigns", "marketing:campaigns", "target", ("marketing:campaign",), True, "Campaigns"),
            NavItem("Content", "marketing:content", "calendar", ("marketing:content", "marketing:post", "marketing:media")),
            NavItem("Blogs", "marketing:blogs", "file-text", ("marketing:blogs",)),
            NavItem("Agents", "marketing:agents", "users", ("marketing:agents",)),
            NavItem("Unified Inbox", "marketing:inbox", "mail", ("marketing:inbox",)),
            NavItem("Reviews", "marketing:reviews", "star", ("marketing:reviews",)),
            NavItem("Analytics", "marketing:analytics", "bar-chart", ("marketing:analytics",)),
            NavItem("Market Research", "insights:research", "search", ("insights:research",)),
            NavItem("Facebook", "marketing:channel", "facebook", ("marketing:channel",), args=("facebook",), section="Social media"),
            NavItem("Instagram", "marketing:channel", "instagram", ("marketing:channel",), args=("instagram",)),
            NavItem("TikTok", "marketing:channel", "tiktok", ("marketing:channel",), args=("tiktok",)),
            NavItem("Google", "marketing:channel", "google", ("marketing:channel",), args=("google",)),
            NavItem("YouTube", "marketing:channel", "youtube", ("marketing:channel",), args=("youtube",)),
            NavItem("Connect Accounts", "marketing:accounts", "settings", ("marketing:accounts",), section="Settings"),
            NavItem("Link Tracking", "marketing:links", "external", ("marketing:link",)),
            NavItem("Automation", "marketing:automation", "refresh", ("marketing:automation",)),
            NavItem("Integrations", "marketing:integrations", "activity", ("marketing:integrations",)),
        ],
    ),
    Module(
        "production", "Trip Production", "camera", "Content production: Sourced → Shot → Posted.",
        {R.ADMIN, R.DIRECTOR, R.MARKETING, R.PRODUCTION_LEAD, R.PRODUCTION_STAFF}, 6,
        [
            NavItem("Overview", "production:overview", "grid", ("production:overview",), True, "Overview"),
            NavItem("Pipeline Board", "production:board", "columns", ("production:board",), True, "Pipeline"),
            NavItem("Shoots", "production:list", "camera", ("production:list", "production:detail", "production:create", "production:edit"), True, "Shoots"),
            NavItem("Asset Library", "marketing:media", "image", ("marketing:media",), True, "Assets"),
            NavItem("Reports", "production:reports", "bar-chart", ("production:reports",)),
        ],
    ),
    Module(
        "admin", "Administration", "settings", "Users, roles, system logs, integration keys and system status.",
        {R.ADMIN, R.ICT_OFFICER}, 1,
        [
            NavItem("System Status", "core:system_status", "activity", ("core:system_status",), True, "Status", frozenset({R.ADMIN})),
            NavItem("Roles & Access", "system:roles", "shield", ("system:roles",), True, "Roles", frozenset(R.EXECUTIVES)),
            NavItem("System Logs", "system:logs", "file-text", ("system:logs",), True, "Logs", frozenset({R.ADMIN, *R.EXECUTIVES})),
            NavItem("Import Data", "system:imports", "download-file", ("system:import",), False, "", frozenset({R.ADMIN})),
            NavItem("Integration Keys", "system:credentials", "settings", ("system:credentials",), False, "", frozenset({R.ADMIN, R.COO, R.DIRECTOR, R.ICT_OFFICER})),
            NavItem("Users & Roles", "accounts:users", "users", ("accounts:user",), True, "Users", frozenset({R.ADMIN})),
            NavItem("Company Profile", "core:company", "building", ("core:company",), True, "Company", frozenset({R.ADMIN})),
            NavItem("Security Settings", "core:security_settings", "shield", ("core:security_settings",), False, "", frozenset({R.ADMIN})),
            NavItem("Install on your phone", "accounts:install", "smartphone", ("accounts:install",)),
        ],
    ),
]

MODULES.append(Module(
    "compliance", "Data Protection", "lock", "Data-subject requests, breach register, anonymisation and retention.",
    {R.ADMIN}, 7,
    [NavItem("Overview", "compliance:dashboard", "shield", ("compliance:dashboard",), True, "Overview"),
     NavItem("Data Requests", "compliance:requests", "inbox", ("compliance:request",), True, "Requests"),
     NavItem("Breach Register", "compliance:breaches", "alert", ("compliance:breach",), True, "Breaches"),
     NavItem("Retention", "compliance:retention", "clock", ("compliance:retention",), True, "Retention")]))

MODULES.append(Module(
    "me", "My Space", "user", "Your private to-do list, calendar, leave and security.", ALL, 1,
    [NavItem("My To-Do", "system:todo", "check-square", ("system:todo", "system:plan"), True, "To-Do"),
     NavItem("Team Plans", "system:team_plans", "users", ("system:team_plans",), roles=frozenset({R.ADMIN, R.COO, R.DIRECTOR, R.SALES_MANAGER})),
     NavItem("My Calendar", "schedule:calendar", "calendar", ("schedule:",), True, "Calendar"),
     NavItem("Budget Requests", "deals:budgets", "wallet", ("deals:budgets",)),
     NavItem("My Attendance", "hr:my_attendance", "clock", ("hr:my_attendance",)),
     NavItem("My Leave", "hr:my", "sun", ("hr:my", "hr:cancel"), True, "Leave"),
     NavItem("Leave Approvals", "hr:approvals", "check-square", ("hr:approvals", "hr:decide"), False, "",
             frozenset({R.ADMIN, R.COO, R.DIRECTOR, R.SALES_MANAGER, R.FRONTDESK_MANAGER, R.OPERATIONS, R.FINANCE, R.MARKETING, R.PRODUCTION_LEAD, R.SUPPLIER_COORDINATOR})),
     NavItem("Notice Board", "notifications:board", "megaphone", ("notifications:board",)),
     NavItem("Notifications", "notifications:list", "bell", ("notifications:list", "notifications:open")),
     NavItem("Tasks & Complaints", "tasks:list", "check-square", ("tasks:",)),
     NavItem("My Email", "mailbox:inbox", "mail", ("mailbox:",)),
     NavItem("My Profile", "accounts:profile", "user", ("accounts:profile", "accounts:security"))]))

MODULES.append(Module(
    "hr", "HR & Leave", "users", "Leave overview, policy, public holidays and approvals.",
    {R.ADMIN, R.COO, R.DIRECTOR}, 8,
    [NavItem("Leave Overview", "hr:overview", "bar-chart", ("hr:overview",), True, "Overview"),
     NavItem("Approvals", "hr:approvals", "check-square", ("hr:approvals", "hr:decide"), True, "Approvals"),
     NavItem("Attendance", "hr:attendance", "clock", ("hr:attendance",)),
     NavItem("Policy & Holidays", "hr:settings", "settings", ("hr:settings",), True, "Policy")]))

MODULES.append(Module(
    "deals", "Deal Desk", "file-text", "KYC checks, e-signatures, payment plans and approvals.",
    {R.ADMIN, R.COO, R.FINANCE}, 9,
    [NavItem("Overview", "deals:overview", "bar-chart", ("deals:overview",), True, "Overview"),
     NavItem("Client KYC", "deals:kyc", "shield", ("deals:kyc",), True, "KYC"),
     NavItem("E-Signatures", "deals:signatures", "edit", ("deals:signature",), True, "Signatures")]))

MODULES.append(Module(
    "ict", "ICT & AI", "whatsapp", "WhatsApp (alerts, reminders, the lead bot), AI settings and usage, webhooks, backups.",
    {R.ADMIN, R.COO, R.ICT_OFFICER}, 10,
    [NavItem("ICT Dashboard", "ict:dashboard", "grid", ("ict:dashboard",), True, "Dashboard"),
     NavItem("Lead Log", "ict:lead_log", "users", ("ict:lead_log",), True, "Leads"),
     NavItem("Chat Logs", "ict:chats", "inbox", ("ict:chat",), True, "Chats"),
     NavItem("Chat Analysis", "ict:analysis", "bar-chart", ("ict:analysis",), True, "Analysis"),
     NavItem("Bot Training", "ict:training", "brain", ("ict:training",), False, ""),
     NavItem("Bulk SMS & Email", "ict:broadcasts", "send", ("ict:broadcast",), False, ""),
     NavItem("Website & Webhooks", "ict:webhooks", "external", ("ict:webhooks",), False, ""),
     NavItem("Work Log", "ict:work_log", "list", ("ict:work_log",), False, ""),
     NavItem("Backups", "ict:backups", "download", ("ict:backup",), False, ""),
     NavItem("Calendar", "ict:calendar", "calendar", ("ict:calendar",), False, ""),
     NavItem("WhatsApp", "ict:whatsapp", "whatsapp", ("ict:whatsapp", "ict:wa_template", "ict:message_log"), False, "", frozenset({R.ADMIN, R.COO, R.ICT_OFFICER}), section="Settings"),
     NavItem("AI", "ict:ai", "sparkles", ("ict:ai",), False, "", frozenset({R.ADMIN, R.COO, R.ICT_OFFICER}), section="Settings"),
     NavItem("API Keys & Bot", "ict:settings", "settings", ("ict:settings", "ict:ai_choice", "ict:bot_toggle", "ict:credential"), False, "", frozenset({R.ADMIN, R.COO, R.ICT_OFFICER})),
     NavItem("Import data", "ict:imports", "download-file", ("ict:import",), False, "", frozenset({R.ADMIN, R.ICT_OFFICER}))]))

MODULES.append(Module(
    "intelligence", "Intelligence", "activity", "Command Centre, sales insights, customer care and market research.",
    {R.ADMIN, R.COO, R.DIRECTOR}, 10,
    [NavItem("Command Centre", "insights:centre", "target", ("insights:centre",), True, "Centre", frozenset(R.ALL_ACCESS)),
     NavItem("Sales Insights", "insights:sales", "trending-up", ("insights:sales",), True, "Sales", frozenset({R.SALES_MANAGER, R.COO, R.DIRECTOR, R.ADMIN})),
     NavItem("Customer Care", "insights:care", "alert", ("insights:care",), True, "Care", frozenset({R.SALES_MANAGER, R.COO, R.DIRECTOR, R.ADMIN})),
     NavItem("Market Research", "insights:research", "search", ("insights:research",), True, "Research", frozenset({R.SALES_MANAGER, R.COO, R.DIRECTOR, R.ADMIN, R.MARKETING})),
     NavItem("Competitor Watch", "insights:competitors", "eye", ("insights:competitor",), False, "", frozenset(R.ALL_ACCESS)),
     NavItem("Chat Analysis", "ict:analysis", "bar-chart", ("ict:analysis",), False, "", frozenset({R.ADMIN, R.COO, R.ICT_OFFICER})),
     NavItem("Team Performance", "coach:team", "users", ("coach:team",), False, "", frozenset({R.SALES_MANAGER, R.COO, R.DIRECTOR, R.ADMIN}))]))

BY_KEY = {m.key: m for m in MODULES}

# every module that has one gets its own User Manual and Calendar entry (views in apps/manuals/module_views.py)
from apps.manuals.keys import CALENDAR_KEYS, MANUAL_KEYS  # noqa: E402

for _k in CALENDAR_KEYS:
    if _k in BY_KEY:
        BY_KEY[_k].nav.append(NavItem("Calendar", f"calendar_{_k}", "calendar", (f"calendar_{_k}",)))
for _k in MANUAL_KEYS:
    if _k in BY_KEY:
        BY_KEY[_k].nav.append(NavItem("User Manual", f"manual_{_k}", "file-text", (f"manual_{_k}",)))


def user_can_access_module(user, key: str) -> bool:
    mod = BY_KEY.get(key)
    if mod is None or not user.is_authenticated:
        return False
    if user.is_superuser or user.role_names & R.ALL_ACCESS:
        return True
    return bool(user.role_names & mod.roles)


def modules_for(user) -> list[Module]:
    return [m for m in MODULES if user_can_access_module(user, m.key)]
