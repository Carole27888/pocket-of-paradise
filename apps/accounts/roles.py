"""Role names (Django Groups) and helpers."""

ADMIN = "Admin"
DIRECTOR = "Director"  # the CEO: read-only everywhere
COO = "COO"  # full read/write wherever a department head would have it
FINANCE = "Finance Officer"
MARKETING = "Marketing Manager"
PRODUCTION_LEAD = "Trip Production Lead"
PRODUCTION_STAFF = "Production Staff"
FRONTDESK_MANAGER = "Front Desk Manager"
RESERVATIONS = "Reservations Officer"
RECEPTIONIST = "Receptionist"
DISPATCHER = "Dispatch Coordinator"
SALES_MANAGER = "Sales Manager"
SALES_AGENT = "Sales Agent"
OPERATIONS = "Operations Coordinator"
GUIDE = "Guide / Driver"
SUPPLIER_COORDINATOR = "Supplier Coordinator"
ICT_OFFICER = "ICT Officer"

ALL_ROLES = [
    ADMIN, DIRECTOR, FINANCE, MARKETING, PRODUCTION_LEAD, PRODUCTION_STAFF,
    FRONTDESK_MANAGER, RESERVATIONS, RECEPTIONIST, DISPATCHER, SALES_MANAGER, SALES_AGENT, OPERATIONS, GUIDE, SUPPLIER_COORDINATOR, ICT_OFFICER, COO,
]

ROLE_DESCRIPTIONS = {
    ADMIN: "Everything, settings, users",
    DIRECTOR: "Read-only across all modules, all reports",
    FINANCE: "Finance module",
    MARKETING: "Marketing module",
    PRODUCTION_LEAD: "Trip Production module (lead)",
    PRODUCTION_STAFF: "Trip Production module",
    FRONTDESK_MANAGER: "Runs the Front Desk: capture, assignment, trip movement, desk reports and the desk team",
    RESERVATIONS: "Front Desk: capture enquiries, assign leads, check guides and vehicles out and in",
    RECEPTIONIST: "Front Desk: capture WhatsApp, walk-in, call and DM enquiries and assign them to agents",
    DISPATCHER: "Front Desk: check guides and vehicles out and in; sees the capture queues read-only",
    SALES_MANAGER: "All of Sales, all agents' bookings",
    SALES_AGENT: "Own enquiries, calls, quotations and performance",
    OPERATIONS: "Fleet & Guides scheduling across all modules",
    GUIDE: "Mobile: today's trips, manifest, check-in, trip expenses and incident reports",
    SUPPLIER_COORDINATOR: "Suppliers module: lodges, camps, transporters, bills and prepayments",
    COO: "Full read/write in every department (acts as every department head). Cannot make anyone Director",
    ICT_OFFICER: "Marketing and the WhatsApp assistant; can assign unassigned leads to agents",
}

# Who may create/edit shared operational records (catalogue, fleet, clients) in Phase 1.
EDITORS = {ADMIN, COO, OPERATIONS, SALES_MANAGER, RESERVATIONS, FRONTDESK_MANAGER}

# Who may add and change tour packages (narrower than EDITORS, which also covers fleet, clients and lodges).
PACKAGE_EDITORS = {ADMIN, COO, OPERATIONS, SALES_MANAGER}

# Who may act on the Front Desk (capture queues, trip check-out/check-in). Directors see it read-only.
DESK_OPERATORS = {ADMIN, COO, FRONTDESK_MANAGER, RESERVATIONS, RECEPTIONIST, OPERATIONS}
# Who may check guides and vehicles out and in (trip movement).
MOVEMENT_OPERATORS = {ADMIN, COO, FRONTDESK_MANAGER, RESERVATIONS, DISPATCHER, OPERATIONS}
# Every role that belongs to the Front Desk department (also receives desk alerts and the daily report).
FRONTDESK_ROLES = {FRONTDESK_MANAGER, RESERVATIONS, RECEPTIONIST, DISPATCHER}

# Sales roles live inside the Sales module only; they never see Operations, Finance, Fleet or Staff screens.
SALES_ROLES = {SALES_MANAGER, SALES_AGENT}
# Who sees every agent's pipeline, calls, quotations and bookings (Directors read-only).
SALES_SEE_ALL = {ADMIN, DIRECTOR, COO, SALES_MANAGER}
# Who may manage the team: assignment rules, targets, reassigning leads.
SALES_MANAGERS = {ADMIN, COO, SALES_MANAGER}

# Finance: who may create/approve finance records. Directors read the whole module.
FINANCE_EDITORS = {ADMIN, COO, FINANCE}
# Who may hand leads to sales agents. Every lead lands at the Front Desk first; only these roles distribute it.
LEAD_DISTRIBUTORS = {FRONTDESK_MANAGER, RESERVATIONS, RECEPTIONIST, SALES_MANAGER, COO, DIRECTOR}
# The social platforms. Marketing only sees leads that arrived through these, to judge which platform sends good leads.
SOCIAL_SOURCES = ["facebook", "instagram", "tiktok", "youtube"]

# Roles that never see the shared Operations module (they work in their own module only).
OWN_MODULE_ONLY = SALES_ROLES | {GUIDE, SUPPLIER_COORDINATOR, MARKETING}

# The all-access roles open every module. The CEO (Director) is read-only; the COO acts as every department head;
# Admin is the technical administrator (a separate axis: system status, imports, compliance tooling, users).
ALL_ACCESS = {ADMIN, DIRECTOR, COO}
# Roles that see the Roles & Access board and System Logs (Admin sees the logs too).
EXECUTIVES = {DIRECTOR, COO}
