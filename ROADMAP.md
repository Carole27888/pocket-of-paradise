# Roadmap

| Phase | Scope | Status |
|---|---|---|
| 1. Foundation + PWA | Settings, DB-by-env, Passenger, roles, shell, enquiries, clients, catalogue, fleet, staff, search, notifications, PWA | ✅ Done |
| 2. Front Desk | Capture queues, trip movement (check-out/in, late alerts), desk report, visitors, appointments, equipment register, shift notes; desk roles (Manager, Receptionist, Dispatch) | ✅ Done |
| 3. Sales | Pipeline board, assignment (rules + round-robin + SLA), calls, follow-ups, versioned quotations (PDF), bookings, payments, double allocation, targets, performance, reports | ✅ Done |
| 4. Finance | Double-entry ledger, invoices (VAT, eTIMS no.), receipts + M-PESA CSV reconciliation, supplier payables, expenses, KRA obligations, payroll, calendar, statements, budgets, reports | ✅ Done |
| 5. Marketing | Channels, campaigns & tracked links, content calendar, unified inbox, reviews, analytics, automation, lead capture webhook + public form (mock integrations) | ✅ Done |
| 6. Trip Readiness & Production | Departures (Planned → Resources ready → Departed → Completed), manifests + check-in, incidents, Sourced → Shot → Posted content pipeline | ✅ Done |
| 8. Platform & governance | RBAC with COO / read-only CEO, Roles & Access board, System Logs, encrypted integration keys, private To-Do, 2FA, data protection, bulk import with undo | ✅ Done |
| 9. People & deals | Calendar, HR leave, KYC, e-signature, payment plans, fixed assets, budget requests, missed-callback follow-ups | ✅ Done |
| 10. AI, public site & reporting | WhatsApp bot + in-app assistant, public catalogue, report registry with scheduled e-mail, in-app manuals + Executive guide | ✅ Done (AI needs a key) |
| 11. Itinerary builder & client journey | Photo library (tagged packs, per-day photo picker), lodge options, extra price lines, inclusions and terms, landscape slide PDF, vouchers, "write for me", client link with Yes/Change/Question, client trip page with payments and feedback, lead journey strip, itinerary nudges, margin guard | ✅ Done |
| 12. Office extras | Bot Training (taught answers), notice board, standard letters, setup checklist, splash screen, Ctrl+K search over itineraries, quotations and pages | ✅ Done |
| 7. Wiring & polish | Cross-module flows, scheduled jobs and alerts, REST API for departures/bookings/guests, role-scoped search, docs, tests | ✅ Done |

## Next

- **Real integrations**: Meta, TikTok, Google Ads, YouTube, TripAdvisor / Google Reviews connections; OTA / booking-channel sync;
  live Meta/TikTok/Google connections; real SMS gateway. (The WhatsApp bot and webhook are built; they need the AI key and WhatsApp token saved under Integration Keys.)
- **M-PESA Daraja live**: C2B callbacks and B2C salary payouts (CSV statement import works today).
- **eTIMS integration**: submit invoices to KRA and store the CU number automatically (the number can be entered by hand today).
- **Offline guides**: today a failed check-in tap is queued in the browser and synced when back online. Next: cache the
  day's manifest for guides (encrypted, expiring) so the full list opens with no signal.
- **Staff clock-in / clock-out** restricted to the office (office network + location check) with monthly attendance and employee
  scorecards; **end-of-day (EOD) reports** per employee; **bulk e-mail campaigns** to collected leads.
- **Instant package builder**: enter hotel rates and number of days → priced package and itinerary generated in seconds.
- **Park-fee tariffs** by season and nationality as data (today park fees are costed through supplier bills).
- **Seat inventory & hold/waitlist** on departures; fuzzy-name matching in double-allocation detection; telephony webhook for calls.
- **Store listings**: the PWA can ship to Google Play as a Trusted Web Activity and be wrapped for the Apple App Store;
  a native guide app can be built on the REST API.
- **HR**: appraisals, recruitment, attendance; **CRM**: corporate / agent accounts. (Leave is built.)
- **Next for e-signature**: stamped signed PDF, reminders before expiry. **KYC**: document OCR. **Assistant**: write actions with confirmation.
