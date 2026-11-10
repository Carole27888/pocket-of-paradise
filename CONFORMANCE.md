# Conformance with `prompt.md`

`prompt.md` describes a Day Safaris system derived from an earlier real-estate codebase ("Realty Boris"). **That codebase was not available
here**: this repository is the Days Safaris Adventures build itself, started from the same architecture (Django 5.2, HTMX + Alpine, hand-written CSS,
database job system, Passenger, PWA). This audit is therefore of *this* repository.

## 1. Reused as-is (unchanged shared foundations)
Settings split + `DATABASE_URL`, `passenger_wsgi.py`, WhiteNoise, database-backed jobs (`apps/core/jobs`), `TimeStampedModel`, `Sequence` numbering,
KES/date formatting, custom User + Groups roles, the service-layer convention, vendored HTMX / Alpine / Chart.js, hand-written CSS design system,
PWA scaffold (manifest, service worker, offline page, `generate_pwa_assets`, iOS splash, install banner), notifications, integrations service layer.

## 2. Renamed / re-pointed
Module registry and nav, KPI/tile components, notification triggers, job names and report titles were re-pointed to safari wording. Palette comes from
the real logo (maroon / sunset orange), not the spec's green (see ASSUMPTIONS.md).

## 3. New domain code (built)
Front Desk (queues, trip movement, visitors, appointments, equipment, shift notes) · Sales (pipeline, assignment engine, calls, quotations, bookings, payments,
double allocation, targets, performance, reports) · Trip Readiness (departures, manifests, check-in, incidents, trip expenses) · Finance (ledger, invoices,
receipts, payables, expenses, KRA, payroll, calendar, statements, budgets, reports) · Suppliers · Marketing (channels, campaigns, content, inbox, reviews,
analytics, tracked links, automation, integrations, lead capture) · Trip Production (content pipeline) · Fleet maintenance log · REST endpoints for departures,
bookings and guests · scheduled jobs and alerts.

## Domain mapping

| Realty Boris (brief) | This repository | Notes |
|---|---|---|
| Property | `catalog.TourPackage` + `trips.Departure` | A package is sold many times; a departure is one dated run with guide, vehicle, manifest |
| PropertyType / Category | `TourPackage.package_type` (group / private / custom / day) | A separate `TourCategory` table was not needed |
| Location / Area | `catalog.Destination` (+ accommodations, rates) | Park fees are costed through supplier bills; date-ranged `ParkFee` tariffs are on the roadmap |
| Client | `contacts.Client` (the brief's "Guest") | Returning-guest detection by phone, e-mail, passport hash; owner agent |
| Lead | `leads.Enquiry` | One shared record for Marketing, Front Desk and Sales |
| Sales pipeline stages | `leads.Stage` (Enquiry → Quoted → Hot → Itinerary sent → Deposit paid → Confirmed → Completed, Lost) | "In progress" is covered by the departure's *Departed* state |
| Sales Agent | `Employee.is_sales_agent` + role *Sales Agent* | |
| Front Desk Officer | Roles *Front Desk Manager*, *Reservations Officer*, *Receptionist*, *Dispatch Coordinator* | |
| Viewing | Not applicable to safaris | Site-visit logic was not needed; departure check-in covers it |
| Agent movement | `frontdesk.TripMovement` (guide / driver / vehicle out and back) | |
| Production pipeline | `trips.Departure` readiness (primary) + `marketing.ContentShoot` (Trip Production) | As the brief recommends |
| Invoice / Receipt | `finance.Invoice`, `finance.Receipt` | |
| Payroll / KRA | `finance.PayrollRun/Line`, `finance.KraObligation`, `finance.TaxSetting` | |
| Supplier payables | `suppliers.Bill`, `BillPayment`, `finance` approval / payment | |
| Campaign / Post / TrackedLink | `marketing.Campaign`, `Post`, `TrackedLink`, `LinkClick`, `AdSpendDaily` | |
| Key register | `frontdesk.EquipmentRecord` (radios, coolers, tents…) | |

## Divergences and gaps (honest list)
- **Different names**: `Client` for Guest, `TourPackage` for Tour, `Booking` (in `sales`) separate from `Enquiry`; the brief's single "Booking" record is the pair Enquiry + Booking.
- **Not built from the brief**: `DepartureSeat` inventory and waitlist, `Season` / `ParkFee` tariff tables, separate `Quote` vs `Offer` objects and `DocumentTemplate` merge-field templates
  (quotation, invoice, receipt, payslip and manifest print views are fixed templates), telephony webhook / click-to-call / `MissedCallback`, fuzzy-name double-allocation matching
  (phone and e-mail via the client record only), per-channel lead-assignment methods beyond rules + round-robin, scheduled report builder UI (a monthly pack is e-mailed instead),
  Google Calendar / Sheets integrations, `Equipment` beyond the register, Lighthouse run, and the live third-party connections (all mocked by design).
- **Extended beyond the brief**: client ownership with sticky assignment, fleet maintenance log, trip expenses from the field, public expo lead capture, bulk stage update,
  brand channel icons, an offline check-in queue.
- **Shared components changed**: `ModuleMixin` / `module_required` accept a list of modules and a per-view edit permission (so Sales can read shared screens without edit rights);
  `Enquiry` gained `deal_value`, `campaign`, `tracked_link`; `Client` gained `owner`; `LeadAssignment.method` gained `owner`.

## Added from the Realty/Boris feature brief (adapted to travel)
Built: COO role and read-only CEO, Roles & Access board, System Logs, encrypted UI-managed integration keys with Test key, private To-Do, reports registry + scheduled reports + PDF/Excel/CSV, 2FA (TOTP),
PWA (existing), in-app manuals + Executive guide, data-protection module, bulk import with undo, calendar with rank-based invites, leave/HR, KYC before deposit, e-signature, payment plans, fixed assets,
WhatsApp AI bot with hand-over, in-app AI assistant, public catalogue site with sitemap, budget requests, missed-callback follow-ups, `GET /api/v1/me/`.
Adapted: "listings/units/tenants" became packages/bookings/clients; property-specific items (landlord statements, tenancy deposit rules) were not applicable and are not built.
Not built: stamped signed-PDF output, live testing of the AI/WhatsApp keys against real accounts (the Test key button exists; no real keys were available), pending-approval referral workflow beyond leave/budget approvals.

## Seed-data mapping
The brief's seed numbers come from design mock-ups that were not supplied; the pipeline counts match the brief (86 / 64 / 38 / 29 / 14 / 9 / 16 + 21 lost, 18 desk enquiries today, 23 unassigned).
Finance, marketing and trips figures are generated through the real services, so they are internally consistent (the books balance) but are not the brief's exact totals.
