# Pocket of Paradise Travel Company — Travel & Tours Management System

*Travel Company.*

Django 5.2 + HTMX + Alpine.js + hand-written CSS. **No Node, npm or build step**: the JavaScript libraries are
vendored in `static/vendor/`, fonts are self-hosted, and the app installs on phones and desktops as a PWA.

**Status: all seven phases built** (Foundation, Front Desk, Sales, Finance, Marketing, Trip Readiness & Production, wiring). See `ROADMAP.md` for what is next, `ASSUMPTIONS.md` for decisions made and `CONFORMANCE.md` for how the build maps to the original brief.

---

## 1. Run it locally (5 minutes)

```bash
python3 -m venv venv && source venv/bin/activate      # Windows: venv\Scripts\activate
pip install -r requirements-dev.txt
python manage.py migrate
python manage.py seed_demo                            # realistic demo data + logins below
python manage.py runserver
```

Open http://127.0.0.1:8000 and sign in. Every demo account uses the password **`Safari@2025`**:

| Email | Role | What they see |
|---|---|---|
| admin@pocketofparadise.co.ke | Admin (+ superuser) | Everything, System Status, Users, Company, Django admin |
| director@ | Director | Read-only across every module and report |
| finance@ | Finance Officer | Finance (invoices, receipts, payables, KRA, payroll, statements) + Suppliers |
| salesmanager@ | Sales Manager | **Sales only**: every agent's pipeline, calls, quotes, bookings, team, targets, assignment rules, double allocation, reports |
| sarah@ · james@ · grace@ · david@ · mercy@ · peter@ | Sales Agent | **Sales only**, and only their own leads, clients, calls, quotes, bookings (David is "Off" and can't be assigned) |
| deskmanager@ | Front Desk Manager | Whole desk: capture, assign, trip movement, visitors, equipment, desk reports |
| frontdesk@ | Reservations Officer | Capture enquiries, assign leads, trip check-out/in |
| reception@ | Receptionist | Capture WhatsApp / walk-in / call / DM enquiries, visitor register |
| dispatch@ | Dispatch Coordinator | Check guides and vehicles out and in (queues read-only) |
| operations@ | Operations Coordinator | Operations, Trip Readiness (departures, manifests, incidents), Fleet, Staff, Suppliers |
| john@ · daniel@ | Guide / Driver | **Trip Readiness only, mobile-first**: their own departures, manifest check-in, trip expenses, incident reports |
| suppliers@ | Supplier Coordinator | **Suppliers only**: suppliers, bills, prepayments (Finance approves and pays) |
| marketing@ | Marketing Manager | Marketing (dashboard, social, campaigns, content, reviews, inbox, links, automation, integrations) + Trip Production |
| production@ · tony@ · diana@ | Trip Production | Content pipeline Sourced → Shot → Posted and the asset library |

`seed_demo --reset` rebuilds the data (about 30 seconds). It creates 26 staff, 12 destinations, 13 lodges/camps, 11 packages with itineraries, 8 vehicles, ~240 clients, 277 enquiries across the pipeline, quotations, bookings, invoices, receipts and payments, ~45 departures with manifests, supplier bills, expenses, three paid payroll runs, KRA obligations, budgets, six campaigns with 45 days of ad spend, reviews, an inbox and a content pipeline. Every money figure is posted through the real double-entry ledger, so the books balance.

Without a `.env`, the dev settings use SQLite (`db.sqlite3`), print e-mail to the console and run background jobs inline.

### Bringing over the old Itinerary Builder

The standalone Itinerary Builder's photo library and templates can be imported once:

```bash
python manage.py import_itinerary_builder "/path/to/ITINERARY BUILDER"
```

It copies photo packs, photos (with tags) and templates (days, terms, extra price lines). Running it twice adds nothing new.
Client PDFs need WeasyPrint and its system libraries (Pango); without them the PDF buttons fall back to the print page.
Enter your bank, Paybill and payment note once under **Administration → Company Profile**: they print on every client itinerary and trip page.

### Tests

```bash
pytest            # ~170 tests: roles & scoping, pipeline, quotes→bookings, double-entry posting, payroll maths, readiness rules, marketing automation, API, every screen on seeded data
```

---

## 2. Deploy on cPanel (Phusion Passenger)

1. **Upload** the project (e.g. to `/home/<user>/dsa`) — not inside `public_html`.
2. cPanel → **Setup Python App** → *Create application*:
   Python 3.11+, *Application root* `dsa`, *Application URL* your (sub)domain,
   *Startup file* `passenger_wsgi.py`, *Entry point* `application`.
3. Copy the "Enter to the virtual environment" command cPanel shows, open **Terminal**, run it, then:
   ```bash
   cd ~/dsa
   cp .env.example .env && nano .env        # SECRET_KEY, ALLOWED_HOSTS, SITE_URL, DATABASE_URL, FIELD_ENCRYPTION_KEY
   pip install -r requirements.txt          # MySQL: pip install -r requirements-mysql.txt
   ./deploy.sh                              # check_database, migrate, collectstatic, check --deploy, restart
   python manage.py createsuperuser         # or: python manage.py seed_demo  (demo only!)
   ```
4. **Cron** (cPanel → Cron Jobs → every 5 minutes). Use the full path to the app's virtualenv Python:
   ```
   */5 * * * * cd /home/<user>/dsa && /home/<user>/virtualenv/dsa/3.11/bin/python manage.py run_jobs >> logs/cron.log 2>&1
   ```
   Admin → **System Status** shows the last heartbeat and turns red if cron hasn't run in 15 minutes.
5. **Restart** after any change: `touch tmp/restart.txt` (deploy.sh does this).

### Database options (`DATABASE_URL`)

| Engine | Example | Notes |
|---|---|---|
| SQLite | `sqlite:////home/user/dsa/db.sqlite3` | Default. Fine for one office. |
| PostgreSQL | `postgres://user:pass@localhost:5432/dsa` | Recommended when available. |
| MySQL/MariaDB | `mysql://user:pass@localhost:3306/db` + `DB_MYSQL_DRIVER=pymysql` | Install `requirements-mysql.txt`. Use utf8mb4 (`DB_OPTIONS`). |

`python manage.py check_database` confirms the connection. `DATABASE_REPLICA_URL` (optional) sends dashboard reads to a replica.

### HTTPS is required
Installing the app, service workers and push notifications all need **https://**. In cPanel use
*SSL/TLS Status → Run AutoSSL*. The production settings redirect HTTP→HTTPS and set HSTS.

---

## 3. Install the app on phones and computers

Staff can open **Install on your phone** (profile menu) for a QR code and instructions.

- **iPhone / iPad**: open in **Safari** → Share → *Add to Home Screen* → Add. A banner reminds iPhone users
  (dismissed for 14 days). Push notifications work on iOS 16.4+ once installed.
- **Android** (Chrome, Samsung Internet, Edge): tap **Install app** in the top bar, or menu ⋮ → *Install app*.
- **Windows / macOS / ChromeOS** (Chrome, Edge): install icon in the address bar or the **Install app** button.
  Safari 17+ on Mac: *File → Add to Dock*.

The installed app opens full-screen with the maroon status bar and splash screen, keeps staff signed in for
30 days, uses a bottom navigation bar on phones and shows a branded **You are offline** page without a connection.
Pages and personal data are never cached offline — only the app shell (CSS, JS, fonts, icons).

### Updating installed apps
Bump `PWA_VERSION` in `.env` and restart. Open apps show *"A new version is available — Refresh"*.

### Push notifications
```bash
python manage.py generate_vapid_keys     # paste both lines into .env, restart
```
Users enable them from the profile menu → *Enable notifications*. New enquiry assignments are pushed to agents.

### Regenerating icons & splash screens
All icons, maskable icons, favicon, Apple touch icon and 32 iOS splash screens are generated from one image:
```bash
python manage.py generate_pwa_assets                                  # uses static/brand/logo-source.png
python manage.py generate_pwa_assets --source path/to/new-mark.png    # square PNG, ≥1024 px, transparent
python manage.py generate_pwa_assets --screenshots path/to/folder     # real wide.png (1280×800) + narrow.png (750×1334)
```
Then bump `PWA_VERSION`.

---

### AI provider

The assistant, the bot and the assistant's wording use Claude when `ANTHROPIC_API_KEY` is saved (Integration Keys), OpenAI when `OPENAI_API_KEY` is saved,
or both: `AI_PREFERRED=claude|openai` picks which goes first and the other is the backup. With no key everything falls back to plain templates.

### Agents' own mailboxes

Each person connects their work mailbox (SMTP to send, IMAP to read) under **My Space → My Email**. Passwords are encrypted at rest; Admin/COO/CEO set a
CC address that is copied on every client email. Set `AGENT_MAIL_BACKEND` only in tests.

## 4. Background jobs

Database-backed, no Redis/Celery. Declare tasks and schedules in any app's `tasks.py`
(`@task`, `@every(minutes=…)`, `@daily(hour=…)`, `@monthly(day=…)`, `enqueue(...)`).

`JOBS_BACKEND=sync` runs jobs inline (development). `cron` queues them for `manage.py run_jobs` (cron every 5
minutes, see section 2), which takes a database lock, retries failures 3 times with backoff and shows failures on
**Admin → System Status** with a Retry button. Schedules registered by the modules:

| Job | When | What it does |
|---|---|---|
| `marketing.response_check` | every 5 min | Fresh leads not contacted within the response SLA move to the next agent; Sales Manager alerted |
| `sales.detect_double_allocations` | every 10 min | Same guest worked by two consultants → Double Allocation screen + manager alert |
| `frontdesk.late_return_alerts` | every 10 min | Guide / vehicle past expected return → Front Desk + Operations |
| `marketing.sync_inbox` | every 15 min | Social messages/comments in; weekend auto-reply; due posts published |
| `finance.invoice_reminders` | daily 08:00 | Balance reminders 30/14/7/2 days before travel; 1/7/14/30 days overdue escalations |
| `trips.departure_alerts` / `prepayment_alerts` | daily 08:00 / 08:30 | Stuck departures, missing manifest, unpaid park fees, missing prepayments |
| `sales.quote_expiry_and_followups` | daily 08:15 | Quotes expiring tomorrow, overdue deposits and follow-ups, expired quotes |
| `finance.deadline_alerts` | daily 07:30 | KRA due in 3 days, bills due, over-budget lines |
| `marketing.pretrip_reminders` | daily 09:00 | WhatsApp pickup/packing reminders at 7 days and 1 day |
| `marketing.sync_stats` / `post_ad_spend` / `sync_reviews` | daily 05:00 – 06:15 | Channel stats, ad spend → Finance expense, reviews, OTA enquiries |
| `marketing.reengage` | daily 10:00 | Lapsed enquiries, expiring social tokens |
| `finance.recurring_invoices` | daily 06:30 | Retainer / corporate invoices |
| `finance.refresh_kra` / `finance.management_pack` | monthly, day 1 | KRA obligations; e-mail the Directors last month's pack |
| `integrations.refresh_exchange_rates` | daily 06:00 | FX rates |
| `fleet.document_expiry_alerts` | daily 07:00 | Insurance / inspection / service due |
| `coach.cycle` | every 10 min | My Assistant: briefings, nudges, manager alerts, end-of-day, weekly and digest messages |
| `ict.process_broadcasts` / `ict.retry_meta_events` | every 1 / 15 min | Bulk SMS & e-mail batches; Meta lead lookups retried |
| `ict.json_backup_due` | every 6 h | Prepares the portable JSON backup when the chosen frequency says one is due |
| `tasks.escalate_overdue` | every 30 min | Overdue tasks and complaints: owner reminded, then manager, then executives |
| `frontdesk.weekend_roster_reminder` | daily 16:00 (acts on Fridays) | Reminds everyone on this weekend's roster of their day and hours |
| `insights.check_competitors` | daily 06:30 | Competitor Watch: re-reads competitors' website pages and Google profiles, alerts watchers about changes |

---

## 5. Modules and project layout

| Module | Who | Highlights |
|---|---|---|
| **Operations** | Admin, Director, Operations, Front Desk roles, Marketing, Finance | Shared records: enquiries, clients, destinations, packages, accommodations (star rating, own tagged photos, delete), fleet (fuel + maintenance log), staff; photo library built from each destination's and accommodation's photos |
| **Front Desk** | Desk roles | Capture queues, trip movement (check-out/in, late alerts), visitor register, appointments, equipment register, shift notes, weekend roster (Friday reminder, shown on My To-Do), daily report |
| **Sales** | Sales Manager / Agents | Pipeline board, assignment (rules + round-robin + SLA), calls, follow-ups, versioned quotations (PDF), bookings, payments, double allocation, targets, performance, reports |
| **Trip Readiness** | Operations, Front Desk Manager, Dispatch, Guides | Departures with the four readiness stages, resource rules, manifests and check-in (offline-tolerant), incidents, trip expenses, reports |
| **Finance** | Finance, Admin (Director read-only) | Double-entry ledger, invoices (VAT, eTIMS no., PDF), receipts + M-PESA reconciliation, payables, expenses with approval, KRA obligations, payroll (PAYE/NSSF/SHIF/AHL), calendar, statements, budgets, 12 reports |
| **Suppliers** | Supplier Coordinator, Finance, Operations | Suppliers, bills, prepayments, departure costs |
| **Marketing** | Marketing Manager | Dashboard, social, channel pages, campaigns, content calendar, reviews, unified inbox, analytics, link tracking, automation, integrations, public lead capture |
| **Trip Production** | Production roles | Sourced → Shot → Posted content pipeline (move forward or back), asset library |
| **My Space** | Everyone | Private To-Do, Calendar (rank-based invites), My Leave, Budget Requests, Reports, User Manuals, Security (2FA) |
| **HR & Leave** | Admin, COO, CEO; managers approve | Leave types and balances (derived), working-day maths with Kenyan public holidays, approvals (no self-approval except the CEO), cancellation flow, calendar sync, analytics |
| **Deal Desk** | Sales, Finance | Client KYC (optional gate before a deposit), e-signature on quotations/bookings (public signing page), payment plans with instalment reminders |
| **Intelligence** | Admin, Director, COO; Sales Manager; Operations | Command Centre, sales insights, customer care, market research, **Competitor Watch** (website change tracking, Google rating and reviews via a Google Places key, AI web searches with sources) |
| **ICT & AI** | ICT Officer, Admin, COO, Sales | WhatsApp lead bot (fixed guardrails, lead capture, hand-over, take-over/resume, analysis) and the in-app assistant (read-only tools, same visibility as the screens) |
| **Data Protection** | Admin | Data-subject requests (30-day clock), breach register, anonymisation, retention runs, public `/privacy/` |
| **Administration** | Admin, ICT (limited) | Roles & Access board, System Logs, encrypted Integration Keys with Test key, Import Data with undo, status |
| **Bulk SMS & Email** | ICT, Admin, COO | Broadcasts to clients, business cards or pasted lists via Africa's Talking (SMS) and Brevo (e-mail): merge fields, test send, scheduling, opt-outs and unsubscribe link, per-recipient log, retry. Simulated until the keys are saved. |
| **Business Cards** | Front Desk | Log name, e-mail, number, category, company and a photo of each card; usable as a bulk-message audience |
| **Fixed Assets** | Finance | Register, straight-line depreciation posted monthly to the ledger |

```
days_safaris/        settings (base/dev/test/prod), urls, wsgi
apps/core/           shell, module registry, jobs, sequences, TableScreen (declarative lists), PDF, seed_demo
apps/accounts/       email login, roles (Groups), users, install page
apps/people/         staff, guides, drivers, departments (incl. Front Desk), duty status
apps/catalog/        destinations, accommodations & rates, packages, itineraries
apps/contacts/       clients (encrypted passport, owner agent), travel party
apps/leads/          enquiries (shared pipeline record), assignment, activities
apps/frontdesk/      desk queues, trip movement, visitors, equipment
apps/sales/          pipeline, quotations, bookings, calls, targets, double allocation, analytics
apps/trips/          departures, manifests, incidents, readiness rules
apps/finance/        ledger, invoices, receipts, expenses, tax, payroll, budgets, statements, reports
apps/suppliers/      suppliers, bills, payments
apps/marketing/      channels, campaigns, posts, reviews, inbox, links, automation, production pipeline
apps/fleet/          vehicles, fuel log, maintenance log, document expiry
apps/notifications/  in-app + web push + outbound SMS/WhatsApp/e-mail log
apps/integrations/   service layer: FX, messaging (SMS/WhatsApp/e-mail), social/ads/reviews/OTA mocks
apps/pwa/            manifest, service worker, offline page, icon generator
apps/system/         audit log, encrypted integration keys, private to-do, roles board, bulk import with undo
apps/compliance/     data requests, breaches, retention
apps/schedule/       calendar events and rank-based invitations
apps/hr/             leave types, policy, requests, balances, public holidays
apps/dealdesk/       KYC, e-signature, payment plans, budget requests, missed-callback job
apps/assets/         fixed assets and depreciation
apps/ict/bulk.py     bulk SMS (Africa's Talking) and e-mail (Brevo): audiences, merge, opt-outs, batches
apps/ict/            WhatsApp bot, in-app assistant, Anthropic client
apps/reports/        report registry, exports (Excel/CSV/PDF), scheduled e-mail reports
apps/manuals/        in-app user manuals and the Executive guide (PDF download)
apps/api/            REST API (/api/v1/) with token + JWT auth
templates/ static/   hand-written CSS design system, vendored JS (htmx, Alpine, Chart.js), fonts, brand & PWA assets
```

Cross-module flows (all covered by tests): enquiry from any channel → shared record → assignment → Sales pipeline;
accepted quotation → booking → invoice; payment → receipt → ledger; booking on a departure → manifest; trip completed →
bookings completed, revenue recognised, prepayments released, review requests sent, content draft created; ad spend →
Finance expense; fuel / maintenance → Finance expense; payroll → KRA obligations → Finance calendar.

Colours live only in `static/css/tokens.css`. Vendored library versions are in `static/vendor/VERSIONS.md`; to update
one, download the new minified file from the package's npm tarball, replace it, update the table, bump `PWA_VERSION`.

## 6. Public endpoints

| URL | Purpose |
|---|---|
| `/explore/` and `/explore/<slug>/` | Public catalogue: search packages, view itinerary, send an enquiry (honeypot + rate limit, goes through the same lead intake). `/sitemap.xml` and `/robots.txt` are generated. |
| `/sign/<token>/` | Client e-signature page (no login, expires after 14 days) |
| `/privacy/` | Privacy notice |
| `POST /webhooks/lead/` | Website form webhook (token in header, bearer, query or `token` field; CORS limited to `WEBHOOK_ALLOWED_ORIGINS`; honeypot fields; 120/h per IP). Ready-made HTML/JS/curl on **ICT → Website & Webhooks** |
| `/webhooks/meta/` | Meta webhook: GET verification with the verify token; POST signed with `META_APP_SECRET` (lead forms, WhatsApp, Messenger, Instagram) |
| `/ict/webhooks/whatsapp/` | Meta WhatsApp Cloud API webhook (GET verify with the saved verify token; POST inbound; set `WHATSAPP_APP_SECRET` to require the signature) |

| URL | Purpose |
|---|---|
| `/capture/?event=Nairobi Travel Expo` | Mobile lead form for the website and expo stands. No login, honeypot + rate limit; creates an enquiry attributed to the event's campaign, assigns it and sends the WhatsApp welcome |
| `/l/<slug>/` | Tracked short link: logs the click, remembers it for attribution, redirects with UTM tags |
| `POST /marketing/webhooks/enquiry/` | Website / OTA enquiry webhook. Header `X-Webhook-Secret: $WEBHOOK_SECRET`; JSON `{name, phone, email?, message?, package_code?, adults?, source?, utm_campaign?}`. Disabled while `WEBHOOK_SECRET` is empty |

## 7. REST API

`POST /api/v1/auth/token/` (email + password → DRF token) or `/api/v1/auth/jwt/`. Endpoints: `destinations/`, `packages/`,
`enquiries/`, `guests/`, `bookings/`, `departures/` (+ `manifest/`, `manifest/<id>/check-in/`), `movement/`. The API honours the
same module access and data scoping as the web app (agents see only their own bookings and guests; guides see only their own
departures; passport numbers are never returned).

## 8. Plug in real integrations

Everything runs in **mock mode** (`INTEGRATIONS_MODE=mock`) with realistic data and no keys. Each integration is a small
class in `apps/integrations/` registered per service name; the live class reads its keys from the environment:

| Service | Environment | Where |
|---|---|---|
| SMS | `AT_USERNAME`, `AT_API_KEY` (Africa's Talking) | `integrations/messaging.py` `LiveSMS` |
| WhatsApp Business | `WHATSAPP_TOKEN`, `WHATSAPP_PHONE_ID` | `LiveWhatsApp` |
| E-mail | `EMAIL_URL` (SMTP) | Django mail |
| Meta / TikTok / Google Ads / YouTube | `META_ACCESS_TOKEN`… | `integrations/social.py` (implement `account_stats`, `daily_ads`) |
| Reviews, OTA | `GOOGLE_REVIEWS_MODE=live` … | `ReviewsService`, `OTAService` |
| FX rates | `FX_PROVIDER_API_KEY` | `integrations/fx.py` |

Set `INTEGRATIONS_MODE=live` (or `<NAME>_MODE=live` per service). Every outbound message is recorded in
*Marketing → Automation → Recent automated messages* so you can see exactly what was sent.
M-PESA statements can be imported today as CSV (*Finance → Receipts → Import*); Daraja C2B callbacks are on the roadmap.
