# Assumptions & decisions

## Branding
- **Palette taken from the real logo**, not the green of the reference mock-ups (those came from an older
  real-estate project). Maroon `#4F1410` (wordmark/acacia/giraffe) is primary; sidebar a deeper `#3B100C`;
  active nav `#8A3A1F` with a sun-amber inset bar; sunset orange `#E8891A` for high-intent buttons (New Enquiry);
  sun amber `#FEB914` and peach `#FFD39A` as highlights; warm sand page background `#FBF7F0`.
  Stage, channel and semantic colours follow the spec. All values live in `static/css/tokens.css`.
- **App icon**: the logo is a 1200×630 banner with text, which is unreadable at icon size. The icon mark
  (`static/brand/logo-source.png`) is a redraw of the concentric sun with the acacia + giraffe silhouette
  extracted from the logo, text removed. The full logo (`logo-full.png`, transparent) is used on the login page;
  the untouched original is kept as `logo-original.png`. Replace `logo-source.png` with a designer's version and
  run `generate_pwa_assets` whenever one is available.
- **Manifest screenshots** are branded placeholders drawn with Pillow (no browser available at build time).
  Take real screenshots after deploy and run `generate_pwa_assets --screenshots <folder>`.
- Fonts: Manrope (UI) and Fraunces (display headings, tagline) are self-hosted woff2.

## Modules and access (all phases)
- Modules: Operations, Front Desk, Sales, Trip Readiness, Finance, Suppliers, Marketing, Trip Production, Administration. A user only sees
  the modules their role allows; **Sales Agent, Sales Manager, Guide / Driver and Supplier Coordinator work in their own module only** and
  never see Operations, Fleet or Staff screens. Shared record screens (enquiries, clients, packages) are opened read-only from Sales.
- Sales Agents see only their own leads, clients (owned or on their leads), calls, quotations, bookings and follow-ups. Guides see only departures
  they guide or drive. Directors are read-only everywhere.
- **Who can edit** shared operational records: Admin, Operations Coordinator, Sales Manager (clients only), Reservations Officer, Front Desk Manager.
  Receptionists and the Sales Manager can add/edit clients. Finance records: Admin and Finance Officer; Suppliers: Admin, Supplier Coordinator, Finance.
- The shortcuts in the PWA manifest are New Enquiry, Today's Departures, Guide Dispatch and Finance Dashboard.

## Data & behaviour
- Phone numbers are stored in E.164 (`+2547…`). Local Kenyan formats (`07…`, `01…`, `7…`, `254…`) are
  normalised; anything with `+`/`00` is kept as international. A foreign phone or non-Kenyan nationality marks
  the client as *diaspora / international*.
- Returning clients are matched on phone (main or alternate), email (case-insensitive) or passport number
  (compared by SHA-256 hash, so the encrypted value is never searched).
- Passport numbers are encrypted with Fernet. If `FIELD_ENCRYPTION_KEY` is empty a key is derived from
  `SECRET_KEY` (dev convenience) — set a real key in production and back it up.
- Reference numbers (`ENQ-00001`, `PKG-0001`) come from a locked `Sequence` row, never `MAX(id)+1`.
- An agent who is **Off** or **On leave** (or inside their leave dates) cannot be assigned enquiries.
- "Stale" means an open enquiry with no stage change for 7 days.
- Exchange rates use a **mock** provider (`INTEGRATIONS_MODE=mock`); set `INTEGRATIONS_MODE=live` and
  `FX_PROVIDER_API_KEY` (exchangerate-api.com format) for real rates.
- Sessions last 30 days; unticking "Keep me signed in" ends the session when the browser closes.
- Login is limited to 5 failed attempts per email + IP per 15 minutes.
- Uploaded media is served through Django to signed-in staff only; `passports/`, `contracts/`,
  `payment-proofs/` and `payslips/` folders need Admin, Finance, Sales Manager or Reservations roles.
- WeasyPrint renders PDFs (quotations, invoices, receipts, payslips). If it or its Pango libraries are missing, the PDF button falls back to a print page.

## Platform, people and AI (phases 8-10)
- **CEO read-only** is enforced by role: Director passes every module gate but has no edit permission; the COO acts as head of every department. Admin keeps technical control.
- **Leave**: annual 21, sick 14 (document needed), maternity 90 and paternity 14 (calendar days, document needed), compassionate 5, study 5, unpaid. Weekends and the listed public holidays are skipped. The
  seeded holidays are the fixed-date Kenyan ones plus Easter for 2026-27; **Admin must confirm each year's gazetted dates** (Eid etc.). Carry-forward is capped at 5 annual days by default. Balances are always
  derived from approved requests, never stored as a running total. Nobody approves their own leave except the CEO.
- **KYC before deposit** is OFF by default (Deal Desk → Overview). When on, only the first payment on a booking is gated; the client's own owner can't verify them.
- **E-signature** records typed name, drawn signature, time and IP against a document hash. It is a simple electronic signature, not a qualified one; confirm with legal that this is enough for your contracts.
- **Depreciation** is straight-line by whole months, starting the month after purchase, posted on the 1st for the month before; re-running a month changes nothing.
- **AI**: the WhatsApp bot and the assistant call the Anthropic Messages API with the key saved under Integration Keys (or `ANTHROPIC_API_KEY`). With no key or no quota the bot politely hands the chat to a
  person and the assistant says it is off. The bot's safety rules are in code (`apps/ict/services.py` GUARDRAILS) and cannot be edited from the screen. It quotes only "from" prices in the published catalogue.
  Message text is sent to the AI provider, so mention that in the privacy notice if you switch it on. Sales roles can see all bot chats (not only their own).
- **Webhook signature**: set `WHATSAPP_APP_SECRET` in production so inbound WhatsApp requests are verified.
- **Scheduled reports** are e-mailed as Excel to the schedule's owner; access is re-checked on every run. Mail goes to the console backend until `EMAIL_URL` is set.
- **Manuals** are written in code (`apps/manuals/content.py`) and cover each module; they are a starting point to adapt to your own procedures.

## Lead routing, bulk messaging and business cards
- **Distribution:** every lead lands at the Front Desk. Only Front Desk (manager, reservations, receptionist), the Sales Manager, the COO and the CEO may hand a lead to an agent; the CEO has no other write access. Auto-assignment exists but is off by default (Sales settings).
- **Marketing** sees only Facebook, Instagram, TikTok and YouTube leads, read-only and without phone or e-mail. DM and Google leads are not counted as "social".
- **Bulk messaging** goes live when the Africa's Talking (username + API key, optional sender ID; username `sandbox` uses their test environment) and Brevo (v3 API key + a verified sender e-mail) keys are saved under Integration Keys. Without them it records everything and sends nothing. SMS cost and Brevo limits are the provider's: check your balance before large sends. Recipients who opt out (unsubscribe link, or the flag on a client or business card) are never messaged. Get consent before marketing to people; Kenya's Data Protection Act applies.
- **Business cards** are personal data: they are included in the data-protection retention review and honour the opt-out flag.

## Demo data
- Logins use `@pocketofparadise.co.ke` and the password `Safari@2025`. Admin, Director, Finance, Sales Manager,
  Reservations and a second driver (Moses Njoroge) are invented names; the rest are from the brief.
- Client names, phone numbers, KRA PINs and bank details are fictitious.

## Sales (Phase 3)
- Pipeline stages are the existing enquiry stages (Enquiry → Quoted → Hot → Itinerary sent → Deposit paid → Confirmed → Completed, Lost).
  Deposit / Confirmed / Completed are reached only through payments and trip completion on the booking, never by dragging a card.
- Quotation validity defaults to 14 days, VAT 16% only when the quote is in KES (export services in USD/EUR/GBP are zero-rated); both are editable in Sales settings.
- A first response is due within 30 minutes (editable). Fresh leads (< 3 days old) breaching it move once to the next available agent; older ones are only flagged.
- Round-robin = the available agent who was assigned a lead longest ago, respecting the daily limit. A returning client's new enquiry goes back to the client's owner agent first.
- Commission on payroll = 2% of revenue won in the month by the agent (rate in Tax settings).

## Trips (Phase 6)
- Departure = a dated run of a tour package. Readiness needs guide, driver, vehicle and five confirmations (accommodation, permits, park fees, meals, insurance).
  "Stuck" = a planned departure within 5 days (`TRIPS_STUCK_DAYS`) still missing resources.
- A guide or vehicle cannot be on two overlapping departures; off-duty guides and vehicles in service cannot be assigned; a vehicle must seat the booked guests.
- The manifest is generated from bookings (lead guest + travel-party members + placeholders for unnamed guests) and never overwrites edited rows. Passport numbers show masked.
- Offline check-in: a failed check-in tap is stored in the browser queue and replayed when the connection returns. The manifest page itself is not cached offline (it holds guest data); see ROADMAP.

## Finance (Phase 4)
- Money is a double-entry ledger; every document posts through one service that refuses unbalanced entries and is idempotent per document event.
- **Revenue recognition**: an issued invoice credits *Tour deposits held* (+ VAT payable); revenue is recognised only when the booking's trip is completed. Prepaid supplier
  costs sit on the balance sheet until the departure completes. Park fees are a cost, not revenue.
- Tax rates, PAYE bands, NSSF/SHIF/AHL, WHT and due days are rows in *Tax settings* seeded with current Kenyan values. **The accountant must confirm them before filing.**
  PAYE = progressive bands on gross less NSSF, SHIF and housing levy, minus KES 2,400 personal relief. NSSF is 6% each side on pay up to the upper earnings limit.
- The tourism / catering levy rate (2%) is a placeholder to confirm; corporation tax instalments (months 4, 6, 9, 12) are estimated from year-to-date profit.
- M-PESA reconciliation imports a CSV (date, amount, reference, account); payments whose account equals an invoice number are matched automatically. Live Daraja callbacks are on the roadmap.
- Fuel logs and maintenance entries become paid expenses automatically; marketing ad spend becomes a paid Marketing expense; guide trip expenses wait for approval.
- Invoices in USD/EUR/GBP keep the booking's exchange rate; receipts settle at that same rate (no FX gain/loss lines).

## Marketing (Phase 5)
- All social / ads / review / OTA / SMS / WhatsApp integrations run in mock mode with deterministic realistic data; mock sends are recorded as messages but nothing leaves the server
  (e-mail goes to the console backend unless `EMAIL_URL` is set).
- The public `/capture/` form attributes a lead to the expo/event named in the link and to the last tracked link the visitor clicked (30-day cookie).
- The WhatsApp chatbot, staff clock-in and EOD reports from the owner's wish-list are **not built**; they are listed in ROADMAP.md.

## Demo data
- Seeded figures are plausible, not the numbers in the original design mock-ups (which were not supplied). Revenue is recognised on trip completion, so a quiet recent month can show low revenue.

## WhatsApp, AI and agent cards (Nov 2026)
- Everything needed to go live is entered on screen: ICT → WhatsApp (Cloud API token, phone number ID, Business Account ID, app secret, verify token, optional API version) and ICT → AI (Claude/OpenAI key and model). Saved keys switch the service live with no environment variable; environment variables still work as a fallback.
- The WhatsApp callback URL is `/ict/webhooks/whatsapp/`. It accepts Meta's handshake with the WhatsApp verify token (or the Meta one) and, once an app secret is saved, only signed deliveries.
- Every in-app alert (`notify()`) is also sent on the person's chosen channel: WhatsApp (default), e-mail, both, or app only. The number is User.whatsapp_number, falling back to the staff record's phone. Delivery runs as a background job after the commit.
- Outside Meta's 24-hour window the system sends the approved template for the message's purpose, falling back to the general `staff_alert` template. Four standard utility templates are provided; they must be submitted to and approved by Meta before alerts reach people who haven't written in.
- A "closed deal" for travel reminders is a booking with status Deposit paid or Confirmed. Reminders go at the configured hour, on each configured day before departure. A booking made late gets only the closest reminder still ahead.
- The business assistant answers only active users whose role is in the configured list (Director and COO by default) and whose profile number matches the sender. It uses read-only tools; any other number gets the customer bot.
- AI cost figures are estimates from editable per-million-token prices.
- Leads from an agent's QR card use the source "Agent QR card" and go straight to that agent. If the agent is on leave they wait at the Front Desk and both the agent and the managers are alerted.
