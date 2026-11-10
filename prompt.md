
Build Prompt: Day Safaris — Tour & Safari Operations Management System (Django + installable PWA)
0. How to use this prompt
You are a senior full-stack Django engineer. Build a complete, production-ready web application called Day Safaris, a management system for a tour and safari company based in Nairobi, Kenya.

This prompt is derived from an existing codebase (Realty Boris — a real estate management system). Day Safaris is the same technical skeleton — same stack, same no-build frontend, same PWA, same deployment, same job system, same design tokens, same user/role pattern, same audit and reporting approach — with the domain swapped from real estate to safaris and tours. Before writing code, read section 0.1 (Conformance) carefully: your job is to confirm and extend what already exists, not to re-invent it.

I am attaching PNG screen designs for the main modules (Bookings, Operations/Fleet, Finance, Marketing, Guest Services / Front Desk, Guides & Fleet). The PNGs are the source of truth for layout, content and look. Match them as closely as possible: sidebar, top bar, cards, tables, charts, status pills, colors and spacing. This document explains the data, logic and rules behind those screens.

Rules for you:

Do not stop to ask questions. When something is unclear, make a sensible assumption, keep going, and record it in ASSUMPTIONS.md.

First, run the conformance audit in section 0.1 and write the result to CONFORMANCE.md. Then build.

Work in the phases in section 11. At the end of each phase the app must run, migrations must apply cleanly, and tests must pass.

Keep code clean, typed where helpful, and organised by Django app. No placeholder "TODO" screens: every screen in the PNGs must exist and work with real data from the database.

Third-party integrations (Meta, TikTok, Google, YouTube, TripAdvisor, Google Reviews, M-PESA, SMS, WhatsApp, payment gateways, booking channels, telephony) must be built behind a service layer with a mock/sandbox implementation so the whole app works without real API keys. Real keys are added later through environment variables.

0.1 Conformance with the existing build (do this first)
The existing Realty Boris codebase already implements the technical foundation. Reuse it. Do not rebuild what already works.

Step 1 — Audit. Clone/inspect the existing repo and produce CONFORMANCE.md with three lists:

Reusable as-is (keep, do not touch): settings split, DATABASE_URL handling, passenger_wsgi.py, WhiteNoise setup, the database-backed job system (apps/core/jobs/), the audit/TimeStampedModel, Sequence numbering model, the KES/date formatting helpers, the custom User + role/Groups pattern, the service-layer convention, the test setup, the vendored frontend (HTMX, Alpine, Chart.js, Manrope), the hand-written CSS design system, the PWA scaffold (manifest, service worker, offline page, generate_pwa_assets, iOS splash screens, install prompt, bottom navigation), reports/, notifications/, integrations/.

Rename / re-point only (change labels, model names, help text, seed data, template copy — no logic change): the app shells for the modules, the sidebar/topbar/nav, the KPI/tile components, the notification triggers, the scheduled-job names, the report titles.

New domain code (actually build): everything in sections 5–10 below that has no equivalent in real estate — Itineraries, Departures, Vehicles, Guides, Park/Park Fees, Bookings, Guests, Suppliers, Tours, etc.

Step 2 — Map the domain. Fill in this table in CONFORMANCE.md (left column is the existing real-estate concept, right is the Day Safaris equivalent). Confirm each mapping before coding:

Realty Boris (existing)	Day Safaris (new)	Notes
Property	Tour / Package (with TourDeparture for dated instances)	A tour is sold many times; a departure is one dated run
PropertyType	TourCategory (Day trip, Multi-day safari, Beach, Mountain, Cultural, Corporate)
Location / Area	Destination / Park (Maasai Mara, Amboseli, Diani, Naivasha…)	Parks carry fees and season rules
Listing type (Sale/Rent)	Trip style / basis (Group, Private, FIT, Charter, Self-drive)
Client (contacts)	Guest (one record per real person)	Returning-guest detection
Lead	Enquiry / Booking request	Same shared pipeline idea
Lead source channel	Enquiry channel (Website, WhatsApp, Instagram, Facebook, TikTok, Google, TripAdvisor, Walk-in, Call, Referral, OTA/Portal)
Sales pipeline stages	Booking stages (Enquiry → Quoted → Deposit paid → Confirmed → Travelled → Closed / Lost)
Sales Agent	Travel Consultant (inside sales)
Front Desk Officer	Guest Services / Reservations Officer
Viewing (property)	Site visit / pre-trip briefing / hotel inspection (kept for B2B and corporate) OR Trip departure check-in	Keep the visit logic for inspections
Agent movement (out/back)	Guide / driver dispatch (vehicle + guide out on a trip, expected back)	Core operational screen
Production (Sourced→Shot→Posted)	Content Production (Sourced → Shot → Posted) or Trip Readiness (Planned → Resources ready → Departed → Completed)	Pick Trip Readiness as primary; keep content production as a sub-module for marketing assets
Invoice / Receipt	Invoice / Receipt (booking deposit, balance, extras)	Same accounting core
Payroll	Payroll (guides, drivers, office staff)	Same calculator, different allowances
KRA obligations	KRA obligations (+ Tourism Levy / catering levy, VAT on tours, withholding tax)	Extend TaxSetting
Supplier payables	Supplier payables (lodges, camps, park fees, transport, caterers, activity providers)	Central to safari ops
Campaign / AdSpend / Post / TrackedLink	Same, plus OTA/booking-channel sync and review monitoring
Property media	Tour media (photos, video, itinerary PDF)
Key register	Vehicle / equipment register (keys, radios, coolers, tents)
Step 3 — Report back. In CONFORMANCE.md, list any place where the existing code does not cleanly map, any existing model you had to generalise, and any assumption you made. Do not silently diverge from the existing patterns — if you must change a shared component, say so and why.

Step 4 — Only then start Phase 1 with the new domain apps. Every phase must end green (runs, migrates, tests pass) exactly as the existing project does.

1. Tech stack (unchanged from the existing build)
Python 3.11+ (minimum 3.10), Django 5.2 LTS

Database selected by DATABASE_URL (SQLite default; PostgreSQL recommended; MySQL/MariaDB supported). Never hard-code engine or connection details.

Frontend: Django templates + HTMX + Alpine.js + a hand-written CSS design system. No Node.js, no npm, no build step.

Charts: Chart.js (vendored UMD build)

Background jobs: database-backed jobs run by cron on Passenger; inline in development. Celery optional only.

PDF: WeasyPrint (invoices, receipts, itineraries, vouchers, tickets, offer letters, statements, reports)

Excel: openpyxl

Filtering: django-filter

API (for a future mobile app / guide app): Django REST Framework, token/JWT auth — exposing tours, departures, bookings, guests, guide dispatch and manifests. Build it alongside the web views.

Config: django-environ, .env.example

Audit: django-simple-history (or custom AuditLog) on key models

Deployment: Phusion Passenger via passenger_wsgi.py + WhiteNoise. WSGI only — no gunicorn, uvicorn, Docker or ASGI.

Development: python manage.py runserver alone runs the app. JOBS_BACKEND=sync (default) runs jobs inline; production uses JOBS_BACKEND=cron.

Locale: TIME_ZONE = "Africa/Nairobi", currency KES, dates 28 Apr 2025, times 2:45 PM, numbers KES 4,850,000

1.1 Frontend with no build step (hard requirement)
Unchanged. The whole app must work with only:

text
pip install -r requirements.txt
python manage.py migrate
python manage.py seed_demo
python manage.py runserver
There must be no package.json, no node_modules, no npm run dev, no Tailwind/Vite/Webpack watcher, and no second terminal. Everything the browser needs is a plain file inside Django's static/ folder.

Vendor the JS as minified files committed to the repo:

text
static/vendor/htmx/htmx.min.js
static/vendor/alpine/alpine.min.js
static/vendor/chartjs/chart.umd.min.js
static/vendor/qrcode/...            # if a browser-side QR is needed; otherwise generate in Python
Record each library's version and source URL in static/vendor/VERSIONS.md.

Self-host the Manrope font as .woff2 in static/fonts/manrope/ with @font-face in CSS. No Google Fonts request.

CSS written by hand, loaded in this order from base.html:

text
static/css/tokens.css      # :root variables: colors, spacing, radius, shadows, font sizes
static/css/base.css        # reset, body, typography, links, focus styles
static/css/layout.css      # app shell: sidebar, top bar, content area, grids, responsive drawer
static/css/components.css  # buttons, cards, KPI cards, tables, pills, badges, forms, tabs,
                           # modals, steppers, progress bars, filter bars, toasts, kanban columns,
                           # itinerary timelines, passenger manifests
static/css/utilities.css   # helpers (flex, gap, text colors, spacing, visually-hidden)
Modern CSS only (custom properties, flexbox, grid, clamp(), media queries). No Sass, no PostCSS.

App JS written by hand in static/js/: app.js (HTMX config, CSRF header on every HTMX request, toasts from HX-Trigger, sidebar drawer), charts.js (reads chart data from a <script type="application/json"> block rendered with json_script, draws with Chart.js). Plain ES modules or IIFEs, no bundler.

Templates: templates/base.html (app shell), templates/components/*.html (included with {% include "components/kpi_card.html" with ... %}), and templates/<app>/partials/*.html for HTMX fragments. Full page on a normal request; partial only when request.htmx is true (django-htmx).

Icons: inline SVG via {% include "components/icon.html" with name="compass" %} backed by one icons.html of stroke SVG paths. No CDN icon font.

Static settings: STATIC_URL, STATICFILES_DIRS = [BASE_DIR / "static"], STATIC_ROOT, WhiteNoise compressed manifest storage in production.

Acceptance test: on a fresh clone with no Node installed, the four commands above must show every screen fully styled, with working charts, tabs, dropdowns, modals, filters and HTMX actions. Add a test that fails if a package.json exists in the repo.

(If the existing build already passes this, keep its files as-is and only add Day Safaris-specific CSS components — itinerary timeline, manifest table, departure board, vehicle status — into components.css.)

1.2 Installable app on phones and computers (PWA — unchanged)
Staff and guides in the field must be able to install Day Safaris like an app on iPhone/iPad, Android, Windows, macOS, ChromeOS and Linux. Once installed it shows the Day Safaris icon, opens full screen without the browser bar, and shows a branded splash screen.

Same rules as the existing build, with these Day Safaris specifics:

Manifest at /manifest.webmanifest (content type application/manifest+json):

name: "Day Safaris", short_name: "Day Safaris", description

id: "/", start_url: "/?source=pwa", scope: "/"

display: "standalone", display_override: ["window-controls-overlay", "standalone"], orientation: "any"

theme_color and background_color: #0B3B2C (or the Day Safaris brand green — confirm against the PNGs)

Icons at 72, 96, 128, 144, 152, 192, 256, 384, 512 px (purpose: "any") plus 192 + 512 maskable

shortcuts: New Enquiry, Today's Departures, Guide Dispatch, Finance Dashboard

screenshots: one wide (desktop) and one narrow (phone)

Assets generated in Python via python manage.py generate_pwa_assets (Pillow), from static/brand/logo-source.png (≥1024×1024, safari mark on transparent), into static/pwa/: all manifest icons, maskable icons, favicon-32.png, favicon.ico, apple-touch-icon.png (180×180, no transparency, brand background), and the full list of iOS splash screens (same device table as the existing build — keep the Python constant and the {% pwa_head %} template tag). Commit the generated files so runserver works immediately.

Head tags via {% pwa_head %} in base.html: manifest link, theme-color, apple-mobile-web-app-capable, mobile-web-app-capable, apple-mobile-web-app-status-bar-style = black-translucent, apple-mobile-web-app-title = Day Safaris, apple-touch-icon, favicons, startup images, and viewport with viewport-fit=cover. Use env(safe-area-inset-*) padding in layout.css.

Service worker at /sw.js (root path so scope covers the app; content type application/javascript, header Service-Worker-Allowed: /, Cache-Control: no-cache):

Versioned cache name from PWA_VERSION, old caches deleted on activate.

Precache the app shell: CSS, vendored JS, fonts, icons, /offline/.

Static files: cache first. HTML and HTMX: network first, offline page fallback for navigations.

Never cache POSTs, login/logout, admin, exports/PDFs, or API responses with personal/financial/guest data.

Register from static/js/app.js; on a waiting version show a toast "A new version is available" with Refresh (skipWaiting + reload).

Install experience: capture beforeinstallprompt and show an "Install app" button in the top bar and on login; hide once installed. iOS Safari: dismissible banner (Share → Add to Home Screen → Add), remembered in localStorage for 14 days. Settings page "Install on your phone" with a QR code (qrcode package) pointing to the site URL plus steps for iOS and Android.

Behaviour when installed: opens into the user's module dashboard (or login), full screen, brand status bar. Phone layout excellent at 360–430px: sidebar becomes a slide-in drawer, plus a bottom navigation bar for the role's 4–5 most-used items (e.g. Guide: Today, My Trips, Manifest, Check-in, More; Consultant: Dashboard, New Enquiry, Bookings, Departures, More). Tables become stacked cards on phones. Tap targets ≥44px. Long-lived secure session cookie ("Remember me"). tel:, https://wa.me/ and mailto: open the native apps. Guides need an offline-tolerant manifest/check-in screen — queue check-ins in IndexedDB/localStorage and sync when back online (record this in ASSUMPTIONS.md and ROADMAP.md).

Web push (optional, wanted): new enquiry assigned, guide late returning, departure missing a manifest, deposit overdue, park-fee deadline, review received. pywebpush + VAPID keys from .env, PushSubscription per user/device, "Enable notifications" toggle. Works on Android/desktop; iOS only after Add to Home Screen (16.4+), so show that hint to iOS users.

Requirements and checks: HTTPS only (localhost allowed for testing). Tests: /manifest.webmanifest returns valid JSON with all fields and existing icon files; /sw.js returns JS with correct headers; /offline/ renders; every manifest file and startup image exists. Lighthouse "Installable" must pass. Document manual steps for Android Chrome, iPhone Safari, iPad Safari, Windows Edge/Chrome, macOS Chrome/Safari.

App stores (future, not now): note in ROADMAP.md that the PWA can later ship to the Google Play Store as a Trusted Web Activity and be wrapped for the Apple App Store, and that a native guide app can be built on the REST API.

1.3 Database determined by .env (hard requirement — unchanged)
Engine and connection selected only through environment variables. If DATABASE_URL is absent, use SQLite at BASE_DIR / "db.sqlite3". Load .env from an absolute path with django-environ, allowing process env to take precedence.

Build DATABASES["default"] with env.db("DATABASE_URL", default=f"sqlite:///{BASE_DIR / 'db.sqlite3'}"), then set CONN_MAX_AGE (DB_CONN_MAX_AGE, default 60), CONN_HEALTH_CHECKS (DB_CONN_HEALTH_CHECKS, default true), and merge JSON DB_OPTIONS into OPTIONS. Support:

dotenv
DATABASE_URL=sqlite:///db.sqlite3
DATABASE_URL=postgres://daysafaris:password@localhost:5432/day_safaris
DATABASE_URL=postgres://user:pass@host:5432/dbname?sslmode=require
DATABASE_URL=mysql://daysafaris:password@localhost:3306/day_safaris
DB_OPTIONS={"charset":"utf8mb4","init_command":"SET sql_mode='STRICT_TRANS_TABLES'"}
psycopg[binary] in requirements.txt; mysqlclient and PyMySQL in requirements-mysql.txt. When DB_MYSQL_DRIVER=pymysql, call pymysql.install_as_MySQLdb() before building settings. If DATABASE_REPLICA_URL is set, configure a replica and route report/dashboard reads to it; otherwise default for all reads.

Keep core ORM portable across SQLite, PostgreSQL and MySQL/MariaDB: no PG-only fields, raw SQL or django.contrib.postgres dependencies. Any PG full-text search guarded by vendor with an icontains fallback. Timezone-aware datetimes (USE_TZ=True, Africa/Nairobi). Generate invoice/receipt/voucher/booking numbers via a Sequence model in transaction.atomic() with select_for_update(), never MAX(id)+1.

check_database prints active engine, host and database name (never the password), then verifies connectivity. Test migrations and seed_demo on each supported engine; document a SQLite/PostgreSQL CI matrix (MySQL where available). Other .env: JOBS_BACKEND, CACHE_URL, EMAIL_URL, SECRET_KEY, DEBUG, ALLOWED_HOSTS, CSRF_TRUSTED_ORIGINS, SITE_URL, media/log paths, integration credentials, VAPID settings. Commented examples for every DB URL in .env.example.

1.4 Deployment on Phusion Passenger (hard requirement — unchanged)
Root passenger_wsgi.py adds the project root to sys.path, sets DJANGO_SETTINGS_MODULE=day_safaris.settings.prod, imports the WSGI application. Keep the ordinary wsgi.py; settings load .env from an absolute BASE_DIR / ".env". No Channels, WebSockets, gunicorn, uvicorn, Docker or ASGI. Live updates use HTMX polling.

WhiteNoise serves collected static. Uploaded media served only through a permission-checking view, never publicly listed. Serve /sw.js, the manifest and /offline/ through Django views. Add tmp/.gitkeep and deploy.sh; deployment installs requirements, migrates, collects static, bumps PWA_VERSION, touches tmp/restart.txt.

apps/core/jobs/: database-backed job system. enqueue(task_name, **kwargs, run_at=None) stores args, status, attempts, run time and last error. Scheduled tasks register with interval/daily/monthly decorators. manage.py run_jobs processes due schedules and queued jobs under a DB lock, retries with backoff up to three attempts, per-run time limit. JOBS_BACKEND supports sync (default), cron, optional future celery. User-triggered slow work queued when appropriate.

Document a five-minute cron entry invoking the venv Python and manage.py run_jobs. Include an Admin-only System Status page with last-run time, failed jobs and Retry actions, and a warning when cron has not run in 15 minutes. Tests for locking, retries, scheduling.

Production settings: DEBUG=False, proxy SSL headers, HTTPS redirect, secure cookies, trusted origins, HSTS. Rotating logs logs/django.log and logs/jobs.log; errors emailed to ADMINS. Branded 400/403/404/500 pages. Tests that passenger_wsgi.application is WSGI-callable and check --deploy passes under production settings. README covers Passenger/cPanel deployment, HTTPS, cron, restart, environment setup.

2. Design system (apply everywhere)
Match the PNGs. Put these in static/css/tokens.css and use everywhere (never hard-code hex in templates). If the existing build already defines these tokens, reuse them; only add the new safari-specific tokens.

Font: Manrope, weights 400–800. Tabular numbers for figures.

Colors

Token	Hex	Use
sidebar	#0B3B2C	Sidebar background
primary	#0E4A36	Primary buttons, headers, active states
primary-hover	#1F7A55	Active sidebar item, progress bars
primary-soft	#E4F3EA	Soft green backgrounds, success pills
page-bg	#F4F6F5	Page background
card	#FFFFFF	Cards, border #E3E8E5
text	#14201B	Main text
text-muted	#5B6B64	Secondary text
danger	#A8201A on #FDE8E7	Overdue, errors, late, cancelled
warning	#8A4B08 on #FDF1DC	Pending, due soon, waitlist
info	#1D4ED8 on #E6EEFD	Scheduled, sent, out, in progress
Enquiry channel colors: WhatsApp #146C43 (WA), Website #1D4ED8 (WB), Instagram #DB2777 (IG), Facebook #2563EB (FB), TikTok #0F766E (TT), Google #B45309 (GO), TripAdvisor #0F766E (TA), Walk-in #7C3AED (WI), Call #1D4ED8 (CL), Referral #B45309 (RF), OTA/Portal #7E22CE (OTA). Shown as small rounded squares with the 2-letter code.

Booking status colors: Enquiry #EEF1EF/#44524C, Quoted #E6EEFD/#1D4ED8, Deposit paid #FFEDD5/#C2410C, Confirmed #F3E8FF/#7E22CE, In progress #CCFBF1/#0F766E, Travelled #E4F3EA/#146C43, Closed won #E4F3EA/#146C43, Cancelled #FDE8E7/#A8201A, Lost #FDE8E7/#A8201A, Waitlist #FDF1DC/#8A4B08.

Departure / trip readiness colors: Planned #8A4B08 on #FDF1DC, Resources ready #1D4ED8 on #E6EEFD, Departed #0F766E on #CCFBF1, Completed #146C43 on #E4F3EA, Delayed #A8201A on #FDE8E7.

Vehicle / fleet status colors: Available #E4F3EA/#146C43, On trip #E6EEFD/#1D4ED8, In service #FDF1DC/#8A4B08, Out of service #FDE8E7/#A8201A.

Layout (unchanged): left sidebar 248px dark green with logo "DAY SAFARIS" + safari mark, "Back to main menu" link, module section title, nav items (44px tall, 8px radius, active filled #1F7A55), optional count badges (orange #C2410C), user card at bottom. Top bar 64px white: global search left, bell with count, avatar + name + role right. Content padding 24px 28px, cards 12px radius, 1px border, 16px gaps. Page header: H1 26px/800, subtitle 13px muted, action buttons right (primary dark green, secondary white with border), ≥44px tall. KPI cards: label, big number, small delta line. Tables: light grey header, 12.5px, row hover, status pills, pagination.

New reusable components (add to components/): tour_card, departure_board, itinerary_timeline (day-by-day), manifest_table (passenger list with check-in), vehicle_status_pill, guide_avatar, guest_pill, season_badge (Peak / High / Low / Green season), price_breakdown, voucher and ticket (PDF-matching), review_stars, park_fee_badge.

Responsive: sidebar collapses to a drawer on tablet/phone; tables scroll horizontally inside their card. Accessible: real <button>/<a>/<label>+<input>, aria-label on icon buttons, 4.5:1 contrast, keyboard usable.

3. Django project structure
text
day_safaris/             # project settings (base, dev, prod)
apps/
  core/                  # shared utilities, base models, audit, KES/date formatting, phone normalisation,
                         # sequence/numbering, jobs, system status
  accounts/              # custom User, roles, permissions, login
  people/                # Employee/Guide/Driver profiles, Department, certifications, languages
  tours/                 # Tour, TourCategory, Itinerary, ItineraryDay, Departure, DepartureSeat/Inventory,
                         # Park, Destination, Season, ParkFee, Activity, AddOn
  fleet/                 # Vehicle, VehicleType, VehicleStatus, VehicleDocument, MaintenanceLog,
                         # FuelLog, TripAssignment, Equipment
  contacts/              # Guest (one record per real person), deduplication, corporate/agent accounts
  bookings/              # Enquiry/Booking (shared by Marketing, Guest Services, Sales), BookingItem,
                         # Traveller/Passenger, Payment, Assignment history, Activity log
  finance/               # accounting core, invoicing, receipts, payables, expenses, KRA, payroll,
                         # calendar, statements, budgets, reports
  marketing/             # channels, campaigns, content, inbox, tracked links, reviews, automation,
                         # analytics, integrations
  operations/            # Trip readiness (departures), manifests, guide dispatch, check-in/out, incidents
  frontdesk/             # guest services: reservations desk, walk-ins, dispatch board, visitors,
                         # appointments, key/equipment register, shift notes
  sales/                 # booking pipeline, assignment engine, double-allocation guard, call tracking,
                         # quotes, offers & confirmations, consultant performance, reports
  suppliers/             # lodges, camps, transporters, caterers, activity providers, park-fee agents
  reports/               # report builder, scheduled reports, exports
  notifications/         # in-app, email, SMS, WhatsApp (service layer + mock)
  integrations/          # Meta, TikTok, Google, YouTube, TripAdvisor/Google Reviews, M-PESA, payment
                         # gateways, OTA/booking channels, SMS, WhatsApp, telephony (mock services)
Every model inherits TimeStampedModel (created_at, updated_at, created_by, updated_by) and supports soft delete where noted. Money is DecimalField(max_digits=14, decimal_places=2).

4. Users, roles and permissions
Custom User (email login) linked to an Employee profile. Roles (Django Groups + permission checks in views and templates):

Role	Access
Admin	Everything, settings, users
Director	Read-only across all modules, all reports
Finance Officer	Finance module
Marketing Manager	Marketing module
Operations Manager	Operations, fleet, guides, trip readiness
Guide / Driver	Mobile-first: today's trips, manifest, check-in, expenses, incident report — no finance or guest PII beyond their trip
Guest Services / Reservations Officer	Front Desk module
Sales Manager / Travel Consultant	All of Sales, all consultants' data (Manager) or own bookings, calls and performance (Consultant)
Supplier Coordinator	Suppliers module only
Sidebar shows only modules the user can access. A module switcher ("Back to main menu") lists all allowed modules. Guide role gets a reduced sidebar and the phone bottom-nav layout by default — this is the role most likely to install the PWA.

5. Shared core models
Employee: user, full name, initials, phone, role/title, department, is_sales_consultant, is_guide, is_driver, daily_enquiry_limit (default 15), specialties (day trips, multi-day, beach, mountain, luxury, corporate, diaspora), languages, guide licence no. + expiry, first-aid cert expiry, on_duty status (In office / On trip / Off / On leave), leave dates, basic salary, bank / M-PESA payout details, KRA PIN, NSSF no., SHIF no.

Tour: code (e.g. TUR-0142), title, category, destinations/parks (M2M), duration (days), trip style (Group / Private / FIT / Charter / Self-drive), min–max pax, price per adult / per child, single supplement, season, inclusions, exclusions, what to bring, cover photo, media, description, itinerary (ordered ItineraryDays), status (Draft, Active, Seasonal, Archived).

TourDeparture: tour, departure date, return date, capacity, seats sold, seats held, status, assigned guide, assigned driver, assigned vehicle, meeting point, pickup list, notes. This is the operational unit — the thing that has a manifest, a guide, a vehicle and a readiness state.

Park / Destination: name, county, country, park fees (per adult/child, resident/non-resident, by season), gate opening hours, permits needed, notes. ParkFee rows are date-ranged so the right fee applies to a departure.

Guest (contacts app): full name, phone (normalised to +2547XXXXXXXX, unique), alt phone, email, nationality, passport no. (for park permits), is_diaspora (auto-true if phone not +254), dietary/medical notes, emergency contact, notes. Returning-guest detection: when an enquiry is created with a phone/email that already exists, link to the existing Guest and show "Returning guest" with their history.

Enquiry / Booking (bookings app, the single record used by Marketing, Front Desk and Sales):

guest (FK), tour of interest (FK, optional), destinations/parks text, preferred travel dates, pax (adults, children, infants)

source channel: Website, WhatsApp, Instagram, Facebook, TikTok, Google, TripAdvisor, Walk-in, Call, DM, Referral, OTA/Portal

captured_by (marketing sync, guest-services officer, consultant), campaign (FK, optional), tracked_link (FK, optional)

purpose: Day trip, Multi-day safari, Beach holiday, Mountain trek, Corporate/team-building, School trip, Honeymoon, Conference/retreat, Transfer only, Other

budget min/max, special requests, notes

stage: Enquiry → Quoted → Deposit paid → Confirmed → In progress → Travelled → Closed (won) / Lost (+ reason) / Cancelled

stage_changed_at (for "days in stage"), last_contact_at, next_action text + due date

booking_status: Confirmed / Provisional / Waitlist

assigned_consultant (current), flags: unassigned, double_allocated

links to BookingItems (tours, add-ons), Travellers (passengers on the booking), and Payments

BookingAssignment: booking, consultant, assigned_by, assigned_at, method (manual, round robin, rule, auto), active, ended_at, end_reason. Full history.

BookingActivity: booking, type (call, WhatsApp, SMS, email, quote sent, payment, itinerary change, note, stage change, assignment), user, timestamp, details. Powers timelines and "last contact".

6. Module: FINANCE
Sidebar: Finance Dashboard, Sales & Revenue, Receivables & Debt, Invoicing, Receipts & Collections, Supplier Payables, Expenses & KRA, Payroll, Finance Calendar, Financial Statements, Budgets, Reports.

Keep the existing accounting core exactly as built — double-entry Account / JournalEntry / JournalLine, automatic posting from every document through a posting service, entries must balance or save fails. Seed a chart of accounts for a Kenyan tour operator: cash & bank, M-PESA float, accounts receivable, prepayments, prepaid park fees, tour deposits held, accounts payable, KRA taxes payable, accrued expenses, bank loan, share capital, retained earnings, tour sales revenue, park-fee recovery, transport income, accommodation commission, activity commission, management fees, other income, guide & driver wages, vehicle fuel, vehicle maintenance, park fees (cost), accommodation (cost), transport (cost), catering, marketing, office rent & utilities, professional fees, depreciation, income tax.

6.1 Screens and logic (safari-specific changes)
Finance Dashboard: KPIs (total revenue, total expenses, net profit before tax, cash & bank with change), position cards (total assets, total liabilities, owed to us, owed to suppliers), income vs expenses bar chart (last 6 months), expense breakdown donut, receivables aging, recent transactions, upcoming deadlines (from calendar), budget vs actual, KRA compliance snapshot, upcoming departures needing supplier prepayment. Date range filter.

Sales & Revenue: revenue KPIs by stream (tour sales, park-fee recovery, transport, accommodation commission, activity commission, other), revenue trend line, revenue by stream bars, sales ledger by booking (date, ref, tour, guest, travel date, pax, deal value, paid, outstanding, consultant, status). Confirmed/travelled bookings from Sales flow in here.

Receivables & Debt Collection: total outstanding, overdue (30+ days), collected this month, average days to collect. Aging buckets 0-30, 31-60, 61-90, 90+ computed from invoice due dates. Debtors table with Remind (SMS/email/WhatsApp) and Record Payment. Payment plans / instalment schedules. Collection activity log. Automatic reminder schedule (configurable): balance reminders before travel date (e.g. 30/14/7/2 days), deposit due reminders, 7 days overdue (task for consultant), 30 days overdue (demand letter PDF).

Invoicing: list with tabs All / Draft / Sent / Part paid / Paid / Overdue / Recurring; auto numbering INV-0001; line items (tour, add-ons, park fees, transport, extras); VAT 16% toggle per line (inclusive or exclusive); invoice PDF with company details, KRA PIN, M-PESA Paybill + account = invoice number, bank details, eTIMS CU number. Send by email/WhatsApp, download PDF, record payment. Recurring invoices for retainer/corporate accounts and monthly service charges (scheduled job on the 1st).

Receipts & Collections: auto numbering RCT-0001; official receipt PDF with amount in words (Kenya Shillings), method (M-PESA, bank/RTGS/EFT, cheque, cash, card, online gateway), reference, invoice balance after payment. Print / email / WhatsApp. M-PESA reconciliation: import statement CSV or receive C2B callbacks (Daraja, mocked), auto-match by account number = invoice number, list unmatched payments with "Match to invoice". Also support online payment gateway and OTA settlement matching.

Supplier Payables: suppliers (lodges, camps, transporters, caterers, activity providers, park-fee agents, airlines), bills (bill no., booking/departure link, category, date, due, amount, paid, balance, status Awaiting approval / Approved / Scheduled / Due this week / Overdue / Paid), approval flow, payables aging, payment runs (select bills, schedule date, mark paid), purchase orders (simple), payment history. Departure-based grouping so the ops team can see "what we still owe for the Mara trip leaving Friday". Withholding tax auto-calculated on professional/contractor bills and added to KRA obligations.

Expenses & KRA Compliance: expense entry with category (fuel, vehicle maintenance, park fees, accommodation, catering, guide wages, office, marketing, other), payee, method, receipt upload (flag "Missing"), approval workflow (Pending, Approved, Paid), petty cash, recurring expenses. Guide trip expenses submitted from the field (fuel, park entry, tips, incidentals) flow here for approval. KRA section is a table of obligations per month:

PAYE (from payroll), Affordable Housing Levy 1.5% + 1.5%, VAT 16% (output VAT from invoices minus input VAT from bills), Withholding Tax, Monthly Rental Income Tax (if any), Corporation Tax instalments, and Tourism Levy / catering levy where applicable.

Each obligation: basis, period, amount, due date, status (Draft, Ready to file, Filed & paid, Scheduled), payment reference, link to iTax.

Tax Compliance Certificate expiry tracker and eTIMS status.

All tax rates, bands, thresholds and due days live in a TaxSetting table editable by Admin, never hard-coded. Seed with current Kenyan values (see 6.3) and show a note that the accountant must confirm them.

Payroll: monthly run with steps Prepare → Review → Approve → Pay & Remit. Employee lines: gross, allowances (trip allowance, per-diem, night-out allowance, guide bonus), deductions, PAYE, NSSF, SHIF, AHL, net pay, pay method (bank / M-PESA B2C). Payslip PDFs, statutory remittance summary (PAYE, AHL, NSSF, SHIF with due dates), advances & loans, history. Sales commissions (from Sales) can be added to the run. Payroll posts to the journal and creates KRA obligations.

Finance Calendar: month grid auto-populated from KRA due dates, payroll dates, invoice due dates, supplier bill due dates, recurring rent dates, budget reviews, month-end close, departure dates and supplier prepayment deadlines; plus manual events. Color by category, "next 7 days" list, reminders by email/SMS 3 days before.

Financial Statements: Income Statement, Balance Sheet, Cash Flow Statement (indirect method) generated from the journal for Monthly / Quarterly / Annual periods, export PDF and Excel. The balance sheet must balance; show the check line.

Budgets: budgets per category for Monthly, Quarterly, Annual; budget vs actual with used %, remaining, status (On track, Over budget, Under budget, Below target for revenue); for annual view a pro-rata marker (expected use to date). Alerts when a line goes over. Approval status.

Reports: report library (income statement, balance sheet, cash flow, aged receivables, aged payables, KRA tax summary, payroll summary, budget vs actual, sales by tour, sales by channel, profitability by departure, supplier spend), recently generated, scheduled reports (monthly management pack on the 1st to Directors).

6.2 Kenyan payroll and tax defaults (seed into TaxSetting, editable)
Same PAYE bands, NSSF (6% + 6%, LEL 8,000, UEL 72,000), SHIF 2.75% (min 300), AHL 1.5% + 1.5%, VAT 16%, Monthly Rental Income Tax 7.5%, Corporation Tax 30% (instalments due the 20th of the 4th, 6th, 9th and 12th months), due days (PAYE/AHL/NSSF/SHIF by the 9th; VAT/WHT/MRI by the 20th). Add safari-specific items: any applicable tourism/catering levy, and confirm park-fee treatment as a cost recovery (not revenue) in the chart of accounts.

Write unit tests for the payroll calculator using: 38,000 / 42,000 / 45,000 / 65,000 / 85,000 and a guide on a lower basic plus trip allowances.

7. Module: MARKETING
Sidebar: Dashboard, Social Media, Enquiries, Campaigns, Content, Reviews, Analytics, Channels · Social Media: Facebook, Instagram, TikTok, Google, YouTube, TripAdvisor · Settings: Connect Accounts, Link Tracking, Lead Assignment, Automation, Integrations.

Models: Channel, SocialAccount (channel, account name/handle, access token encrypted, expires_at, status Connected / Expires soon / Not connected, last_synced_at), Campaign (name, objective, channels M2M, budget, start/end, status, audience notes, creative), AdSpendDaily (campaign, channel, date, spend, impressions, reach, clicks, leads), ChannelStatsDaily (followers, reach, engagement rate, profile views), Post (title, caption, channels, format Reel/Video/Photo/Carousel/Article/Live/Story, tour FK, departure FK optional, scheduled_at, status Draft / Needs approval / Scheduled / Published, published URLs per channel, reach, engagement, leads, bookings attributed), MediaAsset, HashtagSet, InboxMessage (channel, sender, message, type message/comment/review, replied, converted_to_booking FK), TrackedLink (slug, destination, campaign, channel, medium, content, UTM string, QR code), LinkClick, LeadAssignmentRule, AutomationWorkflow + WorkflowStep, Integration, Review (source: Google, TripAdvisor, Facebook, Website; guest name, rating, text, date, replied, reply text, tour/departure FK optional), ReviewRequest (auto-sent after travel).

Screens (match PNGs):

Marketing Dashboard: channel filter chips, enquiries per channel KPIs with deltas, cumulative enquiries line chart per channel, enquiry totals list, enquiry management table (date, name, phone, source, tour interest, travel dates, consultant, status) with filters, campaign performance strip (spend, impressions, clicks, CTR, cost per enquiry, cost per booking), quick stats per selected channel (enquiries, spend, CPL, reach) with enquiries vs spend chart, top performing posts, enquiry sources donut, review score trend.

Social Media: card per channel (followers, growth, posts, engagement, enquiries, spend), scheduled posts table, unified inbox (all channels), best times to post.

Channel page (one template for all channels): KPIs, reach & enquiries by week, audience location, recent posts with Boost, messages & comments with Reply and Convert to enquiry.

Enquiries: pipeline board (New, Contacted, Qualified, Quoted, Confirmed, Closed) reading from the shared Booking model (mapped to Sales stages), drag to move stage, lost enquiries summary.

Campaigns: list with channels, budget use bar, enquiries, CPL, cost per booking, status; detail panel with creative, objective, audience, departures sold, viewings/trips booked; Pause / Duplicate / Edit.

Content: weekly content calendar, approval queue, media library counts and upload, caption & hashtag bank, tour photo/video library.

Reviews: review feed with source, rating, text, guest, tour; reply inline; request-review button; rating distribution; response rate and average response time; reviews per tour; export.

Analytics: funnel Impressions → Clicks → Enquiries → Quotes → Bookings → Travelled; channel comparison (spend, enquiries, CPL, bookings, attributed revenue, return on spend); enquiries per month; what guests ask for (tour/destination interest mix); seasonality view.

Connect Social Accounts: list with status and Connect / Manage, token expiry warning banner.

Link Tracking: builder (destination, campaign, channel, medium, content, short name) → short link + UTM + QR code; table with clicks, enquiries, click-to-enquiry rate. Redirect view /l/<slug> logs the click and redirects.

Lead Assignment: default method (Round robin, By tour type, By destination, By language, By nationality/diaspora, Manual), ordered special rules (e.g. mountain trek → guide-consultant; non-+254 phone → diaspora consultant; corporate → corporate lead; budget > X → senior consultant), consultant availability toggles and daily caps, response rule (not contacted in 15 min → reassign + alert manager). This engine is shared with Guest Services and Sales.

Automation: workflow list with on/off toggles and run counts, and a simple step builder (Trigger → Condition → Action → Wait). Built-in workflows: new enquiry welcome (WhatsApp + assign), consultant response check, pre-trip reminders (packing list, pickup time), post-trip review request + thank-you, re-engagement for guests whose travel date passed without booking, birthday/anniversary offers, daily ad spend recorded as a Marketing expense in Finance, weekend auto-reply.

Integrations: cards for Finance, Sales/CRM (internal), OTA/booking channels, WhatsApp Business API, SMS gateway, email marketing, Google Analytics, Google Calendar, Google Sheets, review platforms (Google, TripAdvisor), property/safari portals (SafariBookings, TripAdvisor, GetYourGuide, Viator, Booking.com), website enquiry forms (webhook endpoint that creates Enquiries).

Integration services: MetaService, TikTokService, GoogleAdsService, YouTubeService, TripAdvisorService, GoogleReviewsService, WhatsAppService, SMSService, OTASyncService — each with a mock implementation generating realistic data so dashboards work offline.

8. Module: OPERATIONS (replaces "Production")
Purpose: get every departure ready and running — the safari equivalent of the production pipeline. Each departure has four readiness states: Planned → Resources ready → Departed → Completed.

Model DepartureReadiness (or extend TourDeparture): tour/departure, planned_by + planned_at, resources_confirmed_at + confirmed_by (vehicle, guide, driver, accommodation, park permits, park fees paid, meals, activities, insurance), departed_at + departed_by, completed_at + completed_by, current_status (computed), days_in_stage (computed), stuck (days_in_stage > configurable threshold, default 5 before departure), delay reason, documents (voucher, itinerary PDF, manifest, park permit, insurance), posting plan (per channel: scheduled / posted, post URL, views). DepartureEvent timeline (planned, resources confirmed, guide assigned, vehicle assigned, permits issued, departed, returned, completed, incident logged).

Screens:

Progress Overview: KPIs Planned / Resources ready (% of planned) / Departed (% of planned) / Completed, avg days from planned → departed vs target; "where departures are right now" stacked bar (waiting on resources, ready to depart, departed, completed); departure progress table with a 4-dot stepper, dates for each stage, status pill, days in stage (red if stuck), status filter chips; Needs Attention list (stuck items + reason — e.g. missing vehicle, unpaid park fees, missing guide); Departures this week.

Departures Board: 4 columns (Planned, Resources ready, Departed, Completed), cards with tour, date, pax, guide → next action, days; move a card forward (HTMX) which stamps the date and user.

Departure Details: big stepper, tour info, guests/manifest, guide & vehicle, itinerary, vouchers & documents, timeline, "Mark as Departed" (requires manifest + guide + vehicle assigned), "Mark as Completed" (requires return, incident log cleared).

Manifests & Check-in: passenger manifest per departure (names, ages, nationalities, passport, dietary/medical, emergency contact, pickup point, checked-in flag), print/PDF, offline-tolerant check-in for guides.

Reports: KPIs vs previous month, weekly output chart (planned / ready / departed / completed), average time per stage vs targets, completion by tour type, team output (ops planners, guides, drivers), on-time departure rate, incidents per 100 trips, fleet utilisation, Weekly / Monthly / Quarterly, export PDF / Excel.

When a departure is marked Completed, create/update the matching Post in Marketing (content production) and trigger the post-trip review request workflow.

9. Module: GUEST SERVICES / FRONT DESK
Sidebar: Guest Services Dashboard, New Enquiry, Enquiries Log, Dispatch & Movement, Visitors & Appointments, Reports. Status key: Checked in / Not checked in (for departures) and Dispatched / Returned (for guides & vehicles).

Core requirement: guest services feeds enquiries from WhatsApp, walk-ins, calls and DMs, assigns them to consultants, sees whether each guest has travelled or not yet, and logs the time a guide/vehicle leaves for a trip and the time they return. Enquiry fields: guest name, phone, tour, date, consultant, purpose.

Models: uses shared Booking/Enquiry. Plus TripDispatch (departure, guide, driver, vehicle, guests, purpose, scheduled_at, status Departed / Returned, outcome note), GuideMovement (guide/driver, departure FK optional, guest, tour, purpose, date, time_out, expected_back, time_in, duration computed, is_late computed), VehicleAssignment (vehicle, departure, guide, driver, out_at, expected_back, in_at, odometer_out, odometer_in, fuel_out, fuel_in), Visitor (name, phone/company, reason, host, time_in, time_out, badge no.), Appointment (title, people, host, start, status), EquipmentRecord (item code: radio, cooler box, tent, binoculars, first-aid kit; assigned to whom, taken_at, due_back, returned_at, status In store / Out / Overdue), ShiftNote, IncidentReport (departure, guide, type, description, severity, action taken, photos, reported_at, resolved_at).

Screens:

Guest Services Dashboard: quick-add buttons per source with today's counts; KPIs (enquiries today by source, not yet assigned, departures today checked in / not checked in, guides/vehicles out now); Guides & Vehicles In / Out board (status, where, since when, due back, late in red); waiting-to-be-assigned queue with suggested consultant (from assignment engine) and Assign; shift note; today's enquiries table with check-in status and sub-note (guide out now, booked 4:30 PM, back 2:20 PM).

New Enquiry: large source selector (WhatsApp, Walk-in, Call, DM), guest name, phone, tour (searchable select), purpose, preferred travel date and time, pax, budget, notes; returning guest banner with history link; consultant picker showing status and load (disabled if off); notify options (SMS to consultant, WhatsApp confirmation to guest); Save & Assign / Save, assign later.

Enquiries Log: filters (month, source, consultant, purpose, tour), toggle All / Travelled / Not travelled with counts, table (date, guest, phone, source, tour, purpose, consultant, travel date, status, action Assign / Remind consultant / Follow up), pagination, export.

Dispatch & Movement: Check Out / Check In form (guide/driver, vehicle, departure, guests, purpose, date, time out auto-filled, expected back, odometer); on check-in the guide marks Departed / Returned + outcome note, which updates the departure and the booking; late return alert banner with Call button; KPIs (departures today, guides/vehicles out now, average time away, late returns); movement log table (guide, vehicle, tour · guests · purpose, time out, time in, away duration, status).

Visitors & Appointments: visitor register with sign in / sign out and badge numbers, today's office appointments timeline, equipment register (issue / return, overdue items red).

Reports: enquiries captured (vs previous month), travelled %, average time to assign vs 15-min target, average time away per trip, enquiries by source with travelled vs not split, enquiries per week, consultant follow-through table (assigned, travelled, not travelled, travel rate, avg time away, late returns), on-time dispatch rate, incident summary. Daily / Weekly / Monthly, export PDF.

Rules: a guide/vehicle marked "Out" cannot be checked out again until checked in; late = now > expected_back and time_in empty (scheduled job every 5 minutes sends an alert); Off / On leave guides cannot be assigned; a vehicle "In service" or "Out of service" cannot be dispatched.

10. Module: SALES
Sidebar: Sales Dashboard, Bookings Pipeline, Double Allocation (badge count), Assign Enquiries (badge count), Consultant Performance, Call Tracking, Quotes, Offers & Confirmations, Reports; pipeline stage key.

Pipeline stages: Enquiry → Quoted → Deposit paid → Confirmed → In progress → Travelled → Closed (won), plus Lost with a reason and Cancelled. Every stage change is logged (BookingActivity) and stamps stage_changed_at.

Models: shared Booking/Enquiry, BookingAssignment, BookingActivity, plus CallLog (consultant, booking, guest phone, direction Outgoing / Incoming, started_at, duration_seconds, outcome Answered / No answer / Busy / Wrong number, stage after call, note, recording URL optional, source manual / click-to-call / telephony webhook), MissedCallback, Quote (booking, tours, add-ons, pax, price breakdown, total, issued_at, valid_until, status Draft / Sent / Accepted / Expired, PDF), Offer (booking, departure, quoted price, offered price, issued_at, valid_until, status), Deal/BookingConfirmation (booking, departure, agreed price, deposit, steps: Quote sent, Accepted, Deposit paid, Confirmed, Travelled, Completed with dates, consultant, sales value, commission rate, commission amount), DocumentTemplate (quote, itinerary, offer letter, booking confirmation, deposit receipt, travel voucher, ticket) with merge fields, SalesTarget (period, metric: sales value, bookings, trips, calls; team or per consultant), DoubleAllocation (booking(s), consultants involved, detected_by phone/email/name, status Open / Resolved, kept_consultant, reason, resolved_by, resolved_at).

Screens:

Sales Dashboard: alert banners (double allocations, enquiries waiting assignment, departures missing seats sold), KPIs (bookings in pipeline, deals closed + conversion, sales value + commission, calls today + talk time), pipeline stage cards with counts, consultant performance grouped bar chart (enquiries, travelled, closed per consultant), targets progress, hot enquiries needing action, quotes & confirmations due.

Bookings Pipeline: stage tabs with counts (All, each stage, Lost, Cancelled), filters (consultant, source, tour type, destination, budget, date added), bulk actions (move to stage, reassign, send WhatsApp), table (guest + phone, source, tour + budget, stage pill, consultant, last contact, next action, days in stage, red if > 7). Consultants only see their own bookings.

Double Allocation: scheduled job detects the same guest (normalised phone, email, or fuzzy name + tour) with more than one active assignment. Card per conflict with each consultant side by side (assigned date, source, activity: calls, quote, stage) and a suggested owner (default rule: the consultant who contacted the guest first; tie-break by most activity). Buttons "Keep <consultant>", "Apply suggested to all". Resolving ends the other assignment(s) and notifies the consultants. Recently resolved log. KPIs: open, resolved this month, most common cause, double calls avoided.

Assign Enquiries: filter unassigned and pending ("proceeding") enquiries by status, stage, source, tour type, destination, budget, received date; table with waiting time (red > 60 min) and suggested consultant dropdown; select and bulk assign; auto-distribute (round robin) respecting availability, specialties/languages and daily caps; consultant capacity panel (new enquiries today vs limit, open bookings, status, Assign here).

Consultant Performance: consultant selector; KPIs (enquiries handled vs team avg, calls made + talk time, trips booked, deals closed + conversion, sales value vs target, commission + rank); weekly activity chart (calls bars, new enquiries line, bookings per week); personal funnel across stages; team comparison table (enquiries, calls, avg call, first response time, travelled, closed, conversion, sales value, commission) with team totals.

Call Tracking: KPIs (calls today in/out, total talk time, average duration, answer rate, missed callbacks); calls by consultant (calls, answered %, talk time, avg duration, share bar); calls by hour chart; outcome breakdown; call log (time, consultant, guest, direction, duration, outcome, stage after call, note). Calls logged manually (quick "Log call" modal from any booking), via click-to-call (tel: link that opens a log form on return), or a telephony provider webhook (mock service). Duration in seconds, shown 4m 08s.

Quotes, Offers & Confirmations: KPIs (open quotes + expiring this week, confirmations in progress, deposits collected, avg quote → confirmed days), deal tracker with 6-step progress bar and next step (red when urgent), document templates that auto-fill from the booking (generate PDF: quote, itinerary, confirmation, voucher), awaiting signatures list. Recording a deposit creates a Receipt in Finance; completing a booking creates revenue + commission entries in Finance and moves the booking to Closed.

Reports: report builder (type, consultant, stage, source, date range, format PDF/Excel, schedule weekly), ready-made reports (pipeline summary, consultant performance, call activity, enquiry sources, double allocation log, stale enquiries 7+ days, quotes & confirmations, lost enquiries with reasons, sales & commission, sales by tour, sales by departure, sales by channel, profitability by departure), recent and scheduled reports.

11. Build phases (do them in order)
Phase 0 — Conformance (do this first). Run the audit in section 0.1, write CONFORMANCE.md with the three lists and the domain-mapping table, confirm what already exists and what must change, and only then begin Phase 1. Report back before writing new code.

Foundation: confirm/reuse project setup, settings split, environment-driven SQLite/PostgreSQL/MySQL, Passenger deployment, database-backed jobs, custom User, roles, Employee/Guide, Tour, TourDeparture, Guest, Booking/Enquiry, BookingAssignment, BookingActivity, the no-build frontend (vendored HTMX/Alpine/Chart.js, self-hosted font, hand-written CSS, components), base layout (sidebar, top bar), login, module switcher, global search. PWA (manifest, generate_pwa_assets, icons, iOS splash screens, service worker, offline page, install button and iOS banner, phone layout with bottom navigation, reduced guide layout).

Guest Services / Front Desk (it creates enquiries and dispatches used everywhere).

Sales (pipeline, assignment engine, double allocation, calls, quotes/offers/confirmations, performance, reports).

Finance (chart of accounts, journal posting service, invoices, receipts, payables, expenses, KRA, payroll, calendar, statements, budgets, reports).

Marketing (channels, campaigns, content, reviews, inbox, tracked links, automation, analytics, integrations with mocks).

Operations (trip readiness, departures board, manifests, guide/vehicle dispatch, incidents).

Fleet & Suppliers (vehicles, maintenance, fuel, documents; supplier bills, prepayments, departures-linked payables).

Cross-module wiring, notifications, scheduled jobs, REST API, final polish, tests, docs.

12. Cross-module data flows (must work end to end)
Marketing enquiry (social, website form, tracked link, OTA sync) → shared Booking/Enquiry → assignment engine → appears in Sales pipeline and Guest Services.

Guest Services enquiry (WhatsApp, walk-in, call, DM) → same Booking model → Sales.

Guest Services check-in marked Departed/Returned → departure status + booking stage updated.

Sales deposit → Finance receipt; booking completed → Finance revenue (commission) + consultant commission → Payroll; supplier costs for the departure → Supplier payables.

Marketing daily ad spend → Finance expense (Marketing category) + VAT/WHT where relevant.

Operations "Completed" → Marketing post record and tour content.

Payroll → KRA obligations (PAYE, AHL) + NSSF/SHIF remittances → Finance calendar deadlines.

Fleet maintenance/fuel → Finance expenses; vehicle assigned to a departure → Operations and Guest Services dispatch board.

All dashboards read live data from these records. No hard-coded numbers in templates.

13. Notifications and scheduled jobs (built-in job system)
New enquiry assigned → consultant SMS/WhatsApp + in-app notification.

Enquiry not contacted within 15 minutes → reassign + alert Sales Manager.

Guide/vehicle late returning from a trip → alert Guest Services + Operations Manager.

Invoice reminders (per schedule), overdue escalation, demand letters.

Balance-due reminders before travel date (30/14/7/2 days).

KRA deadlines 3 days before, payroll approval reminder, supplier bills due, supplier prepayment deadlines before departure.

Quote/offer expiry (1 day before), deposit due, confirmation signing reminders.

Departures missing resources or stuck > 5 days before departure → alert Operations Manager.

Departure missing manifest or unpaid park fees 48 hours before departure → alert.

Double allocation detection every 10 minutes.

Post-trip review request + thank-you; re-engagement for lapsed guests.

Scheduled reports (email PDF/Excel) as configured.

Social account token expiry warnings.

In-app notification center (bell in the top bar) with read/unread.

14. Seed data
Create python manage.py seed_demo filling the database with realistic Nairobi/safari data matching the numbers in the PNGs (April 2025). Adapt the existing real-estate seed command to the safari domain, for example:

Consultants/guides/staff: e.g. Sarah Njeri, James Kariuki, Grace Mumbi, David Mwangi, Mercy Atieno, Peter Odhiambo (consultants); Esther Wanjiku (marketing), Kelvin Sila and Tony Mutua (guides), Diana Kerubo (ops), Brian Kamau (finance).

Tours/destinations: Maasai Mara, Amboseli, Diani, Naivasha, Nakuru, Tsavo, Samburu, Mount Kenya, Lake Victoria, Nairobi city & Karen Blixen, plus corporate/team-building and school trips.

Finance: revenue KES 4,850,000, expenses KES 2,120,000, receivables KES 1,230,000 (aging 540,000 / 310,000 / 215,000 / 165,000), payables KES 640,000, KRA payable KES 1,110,000, 24 employees on payroll, balance sheet total KES 48,200,000.

Marketing: 248 enquiries (FB 72, IG 58, TikTok 46, Google 38, YouTube 34), spend KES 152,480, 1,248,500 impressions, 24,680 clicks.

Operations: 48 planned departures, 34 resources ready, 26 departed/completed.

Guest Services: 18 enquiries today (WhatsApp 7, calls 5, walk-in 4, DMs 2), 9 departures today (5 checked in, 4 not checked in).

Sales: 256 bookings in pipeline (Enquiry 86, Quoted 64, Deposit paid 38, Confirmed 29, In progress 14, Travelled 9, Closed 16, Lost 21), 7 double allocations, 23 unassigned, 142 calls today.

Default login users for each role, listed in the README.

Record the mapping from the old real-estate seed data to the new safari seed data in CONFORMANCE.md so the numbers stay traceable.

15. Quality requirements
Tests (pytest-django): payroll calculator, tax obligations, journal balancing, statements balancing, aging buckets, assignment engine (round robin, rules, caps, availability), double allocation detection and resolution, dispatch check-out/check-in and late logic, departure readiness transitions, manifest/check-in logic, fleet status rules, permissions (consultant/guide sees only own data). Aim for good coverage of services.

Business logic in services.py per app, not in views or templates.

Querysets optimised (select_related, prefetch_related), indexes on phone, stage, assigned_consultant, travel dates, departure date.

Forms validated server-side; phone numbers normalised to +254; money never stored as float.

CSRF, secure cookies, rate limiting on login, secrets from environment, encrypted third-party tokens.

Audit history on Booking, Invoice, Receipt, Payroll, Deal, Expense, Departure, Vehicle.

Every list: search, filter, sort, paginate, export CSV/Excel.

No Node.js or npm anywhere. python manage.py runserver alone serves the complete, styled, interactive app.

The app installs on iOS, Android and desktop with the Day Safaris icon and splash screen, and passes the Lighthouse installable check.

Every PDF (invoice, receipt, payslip, quote, itinerary, confirmation, voucher, ticket, statements, reports) branded with the Day Safaris header, placeholders for KRA PIN, Paybill, bank details, logo.

16. Deliverables
The full Django repository following the structure above, built on top of the existing Realty Boris skeleton (reuse, don't rebuild).

CONFORMANCE.md: the audit (reusable as-is / rename only / new domain code), the domain-mapping table, any divergence from the existing patterns, and the seed-data mapping.

README.md: local setup and step-by-step Passenger/cPanel deployment (clear note that no Node/npm is needed), environment variables including SQLite/PostgreSQL/MySQL examples, seed command, demo logins, cron setup and restart instructions, how to update a vendored JS library, how to install the app on iPhone, Android and desktop, why HTTPS is required, how to regenerate icons and splash screens, and how to plug in real integrations.

ASSUMPTIONS.md: every assumption you made.

.env.example with comments for every setting and database URL example.

Passing test suite.

ROADMAP.md: Play Store listing via Trusted Web Activity and App Store wrapper for the PWA, native guide app on the REST API, offline-tolerant manifest/check-in sync, real Meta/TikTok/Google/YouTube/TripAdvisor/Google Reviews connections, OTA/booking-channel sync, M-PESA Daraja live, eTIMS integration, HR and CRM modules.

Start with Phase 0 (Conformance) now. Report the audit before writing new code, then proceed phase by phase. After each phase, summarise what was built, what was assumed, and how to run it.