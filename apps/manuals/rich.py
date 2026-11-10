"""Step-by-step manuals with real screenshots for every module. `apply()` upgrades the matching manuals in content.MANUALS."""
from .content import Section as S

RICH = {}


def reg(slug, shot_user, sections):
    RICH[slug] = (shot_user, sections)


reg("getting-started", "sarah", [
    S("welcome", "Welcome", intro="This system is where every safari enquiry, quotation, booking, payment and trip lives. It works on any phone, tablet or computer, and you only see what your role needs.",
      bullets=["Sign in with your work e-mail and password. If two-step sign-in is switched on for your role, add the 6-digit code from your authenticator app.", "Your name and role are shown top right. Forgot your password? Ask Admin to reset it.",
               "Everything you do is recorded under your name in the system log."]),
    S("menu", "Your main menu", "Main menu", "Everyone", "The main menu is your home. It shows your briefing, a row of live numbers for your day and a tile for each part of the system your role can open.",
      bullets=["The coloured numbers under the clock card (follow-ups overdue, leads waiting, invoices overdue and so on) open the screen that needs attention.", "Press a tile to enter that area. \"Back to main menu\" at the top of the sidebar returns you here.",
               "The orange **+ New** button in the top bar creates the things your role makes most: a lead, a call, a client, a post."],
      tip="Use the search box at the top to find any client, enquiry, package or reference number from anywhere.", shot="menu", shot_path="/"),
    S("clock", "Clocking in and out", "Main menu → clock card", "Everyone", "Clock in when you start work and out when you finish. The system notes where you were, compared with the office.",
      steps=["Press Clock in. Your browser asks to share your location; allow it.", "You see \"Clocked in · At the office\" or how far away you were, with a map link.", "Press Clock out at the end of the day. My Attendance shows your hours and a 30-day chart."],
      tip="If you block location the clock-in is still saved, marked \"location not shared\", unless your company requires it."),
    S("install", "Install on your phone or computer", "Main menu → profile → Install on your phone", "Everyone", "The system installs like an app, with its own icon, and opens full screen. There is nothing to download from a store.",
      bullets=["Android (Chrome, Samsung Internet, Edge): press Install app in the top bar, or the ⋮ menu → Install app.", "iPhone and iPad (Safari): press Share → Add to Home Screen → Add.",
               "Windows, Mac and Linux (Chrome or Edge): press Install app in the top bar, or the install icon in the address bar. Safari 17+: File → Add to Dock.", "The Install page shows a QR code you can scan from your phone."],
      tip="Installed apps update themselves. A small notice asks you to refresh when a new version is ready."),
    S("alerts", "Notifications and the assistant", intro="The bell shows alerts for you: new leads, follow-ups, signatures, payments. Turn on notifications to get them on your phone even when the app is closed.",
      bullets=["The round chat button (bottom right) answers questions about your own clients, enquiries, follow-ups and packages. It cannot change anything.", "Sales staff also get **My Assistant**: a morning briefing and reminders about pending work."]),
    S("tasks", "Tasks and complaints", "My Space → Tasks & Complaints", "Everyone", "Give work an owner and a deadline. Complaints are tasks that managers hear about the day they are raised.",
      steps=["Press New task, or Log a complaint (also on any enquiry page).", "Choose who does it and when it is due. Managers can give tasks to anyone; staff give them to themselves.", "When it is finished press Done, with an optional note. The person who gave it is told."],
      tip="If a task runs late its owner is reminded, then their manager, then a day later the COO and CEO. Nobody has to chase."),
    S("mail", "Your own email in the system", "My Space → My Email", "Everyone who emails clients", "Connect your work mailbox once. Then write to clients from their enquiry page using templates, and read your inbox and drafts here.",
      steps=["Open My Email → Connect my email and enter your mail server details (SMTP to send, IMAP to read). Press Save; a test email is sent to you.", "On an enquiry press Email, pick a template, edit and send. It is logged on the enquiry."],
      tip="Management's chosen address is copied on every client email. The setting is under My Email → Email copy settings for Admin, COO and CEO."),
    S("security", "Keeping your account safe", "My Space → Security", "Everyone", "Turn on two-step sign-in and keep your recovery codes somewhere safe, not on the same phone.",
      steps=["Open My Space → Security.", "Scan the QR code with an authenticator app (Google Authenticator, Microsoft Authenticator, Authy).", "Enter the 6-digit code to switch it on and save the recovery codes."], shot="security", shot_path="/accounts/security/"),
])

reg("sales", "sarah", [
    S("overview", "How Sales works", intro="Every lead arrives at the Front Desk, who hand it to an agent. From there you work it through the pipeline: Enquiry → Quoted → Hot lead → Itinerary sent → Deposit paid → Confirmed → Trip completed.",
      bullets=["You only see your own leads, calls, quotations and bookings. Sales managers see the whole team.", "Check **My Assistant** each morning. It lists who to call first, follow-ups due and payments to chase."]),
    S("dashboard", "Sales dashboard", "Sales → Sales Dashboard", "Everyone in Sales", "Your numbers at a glance: leads, response times, pipeline value and what needs attention.",
      bullets=["Banners warn about leads waiting too long and double allocations.", "Open a lead to see its next best action and the safaris that match what the client asked for."], shot="dashboard", shot_path="/sales/"),
    S("pipeline", "Pipeline board", "Sales → Pipeline Board", "Everyone in Sales", "Every open lead as a card in its stage. Tick cards and choose a stage to move several at once.",
      steps=["Open the board and find the lead.", "Use the Move to menu on the card, or tick several cards and pick the new stage from the bar that appears.", "Moving to Lost asks for a reason: it feeds the lost-reasons report."], shot="pipeline", shot_path="/sales/pipeline/"),
    S("calls", "Logging calls and follow-ups", "Sales → Calls · Follow-ups", "Everyone in Sales", "Log every call straight after you make it. It starts the response-time clock, keeps the history and counts toward your numbers.",
      steps=["Press Log a call. A pop-up opens.", "Choose the lead, direction, outcome and what was said. Add a follow-up date to create a reminder.", "A busy or unanswered call creates a call-back follow-up automatically two hours later."], shot="calls", shot_path="/sales/calls/"),
    S("itineraries", "Building itineraries", "Sales → Itineraries", "Everyone in Sales", "Build a day-by-day itinerary priced from lodge rates, with photos for each day, lodge options and extra costs, then share it with the client as a link or a PDF.",
      steps=["Open Itineraries → Build an itinerary, choose the lead and start from a blank itinerary, a template or a package.", "Edit the days: lodge, activities, meals, drive time and costs. Press **Write for me** next to a day's description and it drafts the words from what you entered. Drag a day by its handle to reorder, or drag a day in from the template library.",
             "Press **Choose photos** on a day to pick one or two photos from the Photo Library. Search by tag (lion, sunset, tent) or by pack.", "Under **Extra priced lines** add park fees, flights or visas (added to the price without margin). Under **lodge options** add alternative lodges, each with its own price per person and photos: the client picks one, and each gets its own page in the PDF.",
             "Fill in what is included, not included and the payment terms. Press **Save & make PDF** for the landscape client PDF with cover, day-by-day pages, lodge options, pricing and payment details.",
             "Press Create client link. The client opens a designed page with the itinerary, photos and a Pay button, and can press **Yes, I'd like to book this**, ask for a change or ask a question. You are told straight away and a call-back follow-up is created; you see when they open it."],
      tip="Press **Copy** on an itinerary to offer a second option, or save a finished one as a template so the whole team can reuse its days, terms and extra lines.", shot="itineraries", shot_path="/sales/itineraries/"),
    S("library", "The Photo Library", "Sales → Itineraries → Photo Library", "Everyone in Sales and Operations", "A pack of tagged photos for each destination and each lodge. Photos are shrunk on upload so PDFs stay small enough to email.",
      steps=["Open Photo Library and pick Destinations or Lodges & hotels. Type a name and press **Create pack**.", "Open the pack and drag photos onto the box (as many as you like). Add tags such as lion, sunset, tent so you can find them later.", "To retag or remove photos, tick them and use the bar above the grid. Untagged photos are listed under the Untagged chip."],
      tip="Tag as you upload: a tagged pack makes choosing photos for a day a ten-second job."),
    S("journey", "Where is this lead on the way to a booking?", "Sales → any lead", "Everyone in Sales", "The strip at the top of every lead shows six steps: Enquiry, Itinerary, Proposal sent, Client says yes, Deposit paid and Trip done. Done steps are ticked and the one you should work on next is outlined; steps with a record link to it.",
      bullets=["When a client presses Yes on their link the lead warms up to Hot, a call-back is added to your follow-ups and you get a notification.", "Each morning the system nudges you about itineraries the client never opened, ones they keep opening with no reply, and itineraries sent with no quotation behind them."]),
    S("portal", "The client's trip page and feedback", "Sales → Bookings → a booking → Client trip page", "Everyone in Sales", "Give a booked client one page for their trip: what they have paid and what is due, how to pay with their booking number as the reference, the itinerary, what documents and clothes to bring, and your contact. After they travel it asks them to rate the trip.",
      steps=["Open the booking and press **Create client trip page**.", "Copy the link or press **Send on WhatsApp**. The page needs no password; only people with the link can open it.", "After the trip, ratings appear under **Client Feedback**. A low score alerts the sales manager and puts a call on your follow-ups; a high one reminds you to thank the client and ask for a review."],
      tip="Keep your bank and Paybill details up to date under Administration → Company Profile: they are printed on the trip page and on client itineraries."),
    S("quotes", "Quotations and e-signature", "Sales → Quotations", "Everyone in Sales", "Create a quotation from an itinerary or lead, send it, and ask the client to sign on their phone.",
      steps=["From the itinerary press Create quotation, review the lines and save.", "Send it by WhatsApp or e-mail. The lead moves to Quoted.", "Press Send for e-signature. The client types their name and draws their signature; you are notified.", "When they accept, press Accept to create the booking and the deposit amount."], shot="quotes", shot_path="/sales/quotations/"),
    S("bookings", "Bookings, deposits and payment plans", "Sales → Bookings", "Everyone in Sales", "Record payments as they arrive, set up instalments, and watch the booking move to Deposit paid and Confirmed.",
      steps=["Open the booking and record the deposit (M-PESA, bank, card or cash).", "For long lead times press Payment plan and choose the number of instalments. Clients get an SMS before each is due.", "Attach the booking to a departure so Trips can plan vehicles and guides."], shot="bookings", shot_path="/sales/bookings/"),
    S("assistant", "My Assistant and Team Performance", "Sales → My Assistant · Team Performance", "Everyone in Sales", "Your assistant briefs you each morning, nudges you about stale leads and unanswered quotations, and sends an end-of-day report. Team Performance ranks the team by progress to target each week.",
      bullets=["Reply to a nudge with a reason (for example, \"client travelling, call Friday\"). It is saved on the lead.", "Customer Care lists your leads at risk of poor service, with the reason.", "Targets are set by your manager under Sales → Targets."], shot="team", shot_path="/coach/team/"),
    S("insights", "Sales Insights and Market Research", "Sales → Sales Insights / Market Research", "Sales Manager, COO, CEO, Admin (Research also Marketing)", "Sales Insights shows who is on pace for target, who needs support and why (with the evidence), and which deals have stopped moving. Market Research shows what travellers ask for: rising and cooling destinations, markets, requested months and why deals are lost. No client names appear in Research.",
      tip="A low number is a reason to help, not to blame. Early in the month the outlook is marked low confidence."),
    S("routing", "How new leads are routed", "Sales → Assignment", "Sales Manager, COO, Admin", "A new lead goes to the client's owner if they are available, then the first matching rule, then an agent whose specialties match the destination or interest, then round robin. Set an agent's specialties under Staff & Guides.",
      tip="Turn Route by specialist off in Assignment settings if you prefer plain round robin."),
])

reg("marketing", "marketing", [
    S("overview", "How Marketing works", intro="Every lead from the social platforms lands at the Front Desk first. Marketing tracks where those leads come from and how far they get, so you can see which platform sends good leads.",
      bullets=["You see only leads from Facebook, Instagram, TikTok and YouTube, without phone numbers or e-mails.", "Marketing does not assign or move leads; Sales does."]),
    S("leads", "Social leads and lead quality", "Marketing → Social Leads · Lead Quality", "Marketing", "See every social lead and its journey, and compare platforms by how many leads reach a quotation, a booking and a confirmed trip.",
      bullets=["Lead Quality ranks platforms by lead-to-booking conversion, with the number lost and the average days to book.", "Open any lead to see its lifetime: when it arrived, how long it stayed in each stage and how quickly it was handled."], shot="quality", shot_path="/marketing/lead-quality/"),
    S("calendar", "Posting calendar", "Marketing → Posting Calendar", "Marketing", "Plan and schedule posts on a month calendar.",
      steps=["Hover a day and press + to schedule a post: title, caption, channels, media and time.", "Drag a post (or a draft from the sidebar) onto another day to reschedule it; the time of day is kept.", "Posts waiting for approval appear in the approval queue."], shot="calendar", shot_path="/marketing/content/"),
    S("campaigns", "Campaigns and Meta ads", "Marketing → Campaigns", "Marketing", "Track budget, spend and what each campaign brought in.",
      steps=["Create the campaign with its budget and dates.", "Paste the Meta campaign, ad set, ad or lead-form IDs into the Meta IDs field. Leads from those ads are then credited to the campaign automatically.", "Use tracked links with a QR code for offline material; a lead who later fills the website form is credited to the link."], shot="campaigns", shot_path="/marketing/campaigns/"),
    S("inbox", "Unified inbox", "Marketing → Unified Inbox", "Marketing", "Messages and comments from every channel in one place. Messenger and Instagram DMs arrive here automatically once Meta is connected.", shot="inbox", shot_path="/marketing/inbox/"),
])

reg("ict", "ict", [
    S("backup", "Data backup", "ICT → Data Backup", "ICT, Admin, COO", "A plain JSON copy of all business data that you download and keep on another computer or drive. It is prepared automatically on the schedule you choose (daily, weekly or monthly).",
      steps=["Open Data Backup. Choose how often and how many files to keep, then Save.", "When ICT is told a backup is ready, press Download and store the file somewhere safe. The first download closes it.", "Press Create a backup now any time, for example before a big import."],
      tip="Saved integration keys are never in the file. Treat it as confidential: it holds client details."),
    S("ai", "Choosing the AI provider", "Administration → Integration Keys", "ICT, Admin, COO", "The assistant and the WhatsApp bot can use Claude (Anthropic) or OpenAI. Save either key, or both; the other is the backup if one is down.",
      steps=["Save the Claude key under AI assistant (Anthropic Claude), and/or the OpenAI key under AI backup (OpenAI).", "To prefer OpenAI, type openai in the Preferred provider box of the Claude entry. Leave it empty to prefer Claude."]),
    S("overview", "What ICT looks after", intro="ICT connects the system to the outside world: WhatsApp, the website's enquiry forms, Meta ads, SMS and e-mail. Keys are saved once under Administration → Integration Keys and tested with Test key.", bullets=["Nothing is sent to anyone until the relevant key is saved; until then everything is simulated and recorded."]),
    S("leadlog", "Lead Log: every lead in one place", "ICT → Lead Log", "ICT, Admin, COO", "One list of every lead from Facebook, Instagram, TikTok, YouTube, the website, WhatsApp and the front desk, with where it came from, who captured it and who is handling it. It reads the same record the Front Desk and Sales use, so it never disagrees with them.",
      steps=["Use the tabs to look at Social media, Front desk, WhatsApp bot or Website & Google on their own.", "Search by name, phone, reference, destination or campaign, or filter by source, stage and dates.", "Press Show on the amber banner to see leads nobody has been assigned yet.", "Press CSV or Excel to download exactly what is on screen."],
      tip="This page only shows leads. To change a lead, open it from here (if your role allows) or ask the Front Desk."),
    S("sources", "Adding a lead source", "Sales → Lead Sources", "COO, Admin", "Add where leads come from beyond the built-in ones: a travel expo, a roadshow, a radio ad, a partner event. Once added, the source can be picked on a new lead and appears in filters, the Lead Log and reports.",
      steps=["Open Lead Sources and type the name, for example Nairobi Travel Expo. Add two or three letters for the badge if you like.", "Press Add source. It is available straight away when capturing a lead.", "Press Hide when an event is over. Old leads keep their source; it just stops being offered on new ones."],
      tip="A source that already has leads cannot be deleted, only hidden, so history stays correct."),
    S("training", "Teaching the WhatsApp bot", "ICT → Bot Training", "ICT, Admin, COO", "Write the questions customers ask and the answers you approve. The bot uses a clear match word for word, so it gives the same answer every time, and it still works when the AI is switched off or out of credit.",
      steps=["Open Bot Training and press **Learn from the package catalogue** to add the basics (length, price, what is included) for every published package.", "Under **Teach something new** write what the customer asks, the answer, and the other words people use for it. Press Teach the bot.", "Use **Try it** to ask as a customer would and see which answer the bot would give.", "Check **Customers asked, bot can't answer** each week. Press Teach on a question to turn it into an answer."],
      tip="Keep answers short and factual. The bot never invents prices; anything not taught goes to the AI under the built-in safety rules, or to a person."),
    S("webhooks", "Website forms and Meta webhooks", "ICT → Website & Webhooks", "ICT, Admin, COO", "Every enquiry form on the website, and every Facebook or Instagram lead ad, creates a lead at the Front Desk within seconds.",
      steps=["Open Website & Webhooks. Copy the endpoint and token, or the ready-made HTML form, into your website. Press Send a test lead and check the Front Desk waiting list.", "For Meta: paste the callback URL and verify token into your Meta app's webhook settings, save the app secret and page access token here, and subscribe the page to leadgen and messages.",
             "Send a test lead form or Test Click-to-WhatsApp to prove the path end to end.", "Recent deliveries lists every call with its result; failed Meta lookups are retried automatically every 15 minutes."],
      tip="If a form stops working, check Recent deliveries: a 401 means the token on the website is out of date.", shot="webhooks", shot_path="/ict/webhooks/"),
    S("bulk", "Bulk SMS and e-mail", "ICT → Bulk SMS & Email", "ICT, Admin, COO", "Write once, send to many through Africa's Talking (SMS) or Brevo (e-mail).",
      steps=["Press New SMS or New e-mail.", "Write the message. {first_name}, {name} and {company} personalise it.", "Choose the audience: clients by channel, stage and market; business cards by category; or pasted numbers or addresses.", "Send a test to yourself, then Send now or schedule. Confirm the recipient count."],
      tip="Opted-out people and invalid numbers are skipped automatically and listed on the broadcast page.", shot="bulk", shot_path="/ict/broadcasts/"),
    S("chats", "WhatsApp chats and the bot", "ICT → WhatsApp Chats", "ICT, Sales Managers", "The bot answers new WhatsApp messages from the published catalogue and hands over when someone needs a person.",
      steps=["Open a chat to read it. Press Take over to reply yourself, Resume bot to hand it back.", "In Bot Settings tick Bot replies automatically once the AI key and WhatsApp token are saved."], shot="chats", shot_path="/ict/chats/"),
])

reg("finance", "finance", [
    S("overview", "How Finance works", intro="Every invoice, receipt, bill, payroll run and depreciation posts to a double-entry ledger that must balance. Revenue is recognised when a trip is completed.", bullets=["Entries are never edited: a mistake is corrected by a reversing entry."]),
    S("dashboard", "Dashboard", "Finance → Dashboard", "Finance, COO, CEO (read-only)", "Cash, receivables, payables and what is due this week.", shot="dashboard", shot_path="/finance/"),
    S("invoices", "Invoices and receipts", "Finance → Invoicing · Receipts", "Finance", "Invoices come from bookings (VAT 16%). Record each payment against its invoice.", steps=["Open the invoice and press Record payment.", "M-PESA statements can be imported and matched by invoice number.", "Send reminders from Receivables for overdue invoices."], shot="invoices", shot_path="/finance/invoices/"),
    S("payables", "Supplier bills and payment runs", "Finance → Supplier Payables", "Finance", "Enter bills, approve them and pay several in one payment run. Withholding tax is deducted automatically where it applies."),
    S("assets", "Fixed assets and depreciation", "Finance → Fixed Assets", "Finance", "Register assets with cost, residual value and life. Depreciation posts on the 1st of each month for the month before.", shot="assets", shot_path="/assets/"),
    S("statements", "Statements and reports", "Finance → Financial Statements · Reports", "Finance, COO, CEO", "Income statement, balance sheet and cash flow for any period, and every report exportable to Excel or PDF."),
])

reg("trips", "operations", [
    S("overview", "How Trips works", intro="A departure moves Planned → Resources ready → Departed → Completed. Bookings attach to a departure; vehicles, drivers and guides are assigned to it.", bullets=["The system warns about double bookings and expired licences, insurance or certifications."]),
    S("departures", "Departures and readiness", "Trips → Departures", "Operations", "See what is leaving, what is missing and who is booked.", shot="departures", shot_path="/trips/"),
    S("manifest", "Manifests and check-in", "Trips → open a departure", "Operations, Guides", "Print the manifest, check guests in (works offline and syncs later) and log incidents and trip expenses."),
    S("complete", "Completing a trip", intro="Mark the departure Completed when the trip ends. Revenue is recognised, prepayments are released, review requests go out and a content draft is created for Marketing."),
])

reg("hr", "coo", [
    S("overview", "Leave and attendance", intro="Staff request leave, managers approve it, and balances are always worked out from approved leave. Attendance shows who clocked in and from where."),
    S("approvals", "Approving leave", "HR → Approvals", "Managers, COO, CEO", "Approve or decline with a note. Nobody approves their own leave except the CEO. A cancellation request on approved leave is a separate line to confirm.", shot="approvals", shot_path="/hr/approvals/"),
    S("attendance", "Attendance", "HR → Attendance", "HR, COO, CEO", "Pick a date to see who clocked in, when, from where and how many hours, plus who has not clocked in.", shot="attendance", shot_path="/hr/attendance/"),
    S("policy", "Policy, holidays and offices", "HR → Policy & Holidays", "HR, COO, CEO", "Set days per leave type, the carry-forward cap, public holidays (confirm each year's gazetted dates, such as Eid), office locations for clock-in and the lateness rules.", shot="policy", shot_path="/hr/settings/"),
])

reg("admin", "admin", [
    S("overview", "Administration", intro="Admin looks after people, access, keys and the health of the system."),
    S("roles", "Users and roles", "Administration → Roles & Access", "Admin, COO, CEO", "See who holds each role and move people between roles. Every change is logged.", shot="roles", shot_path="/system/roles/"),
    S("keys", "Integration keys", "Administration → Integration Keys", "Admin, ICT, COO", "Keys for AI, WhatsApp, Meta, SMS, e-mail and M-PESA are stored encrypted and shown masked. Press Test key after saving: it says whether the key is valid, unauthorised or out of credit.", shot="keys", shot_path="/system/keys/"),
    S("logs", "System logs and backups", "Administration → System Logs", "Admin, COO, CEO", "Every sign-in, change and key update is recorded with who, when and from where."),
    S("imports", "Importing data", "Administration → Import Data", "Admin", "Download the template, fill it, upload, check the preview, then commit. Every import can be undone as one batch."),
])

reg("executive-guide", "director", [
    S("access", "Your access", intro="The CEO and COO can open every department. The CEO's access is read-only by design, except handing leads to agents. The COO acts as head of every department.", bullets=["Roles & Access shows who holds which role; System Logs shows every action by person and time."]),
    S("centre", "Command Centre", "My Space → Command Centre", "CEO, COO, Admin", "Revenue against target, open pipeline, what needs attention (with the evidence and a link) and **Ask the business**: questions answered only from live figures.", shot="centre", shot_path="/insights/"),
    S("week", "Five checks each week", bullets=["Sales: pipeline by stage and agent performance; look for leads stuck in Quoted.", "Money: invoices over 14 days old and budget requests waiting on you.", "Trips: next fortnight's departures have a vehicle, driver and guide.", "People: leave waiting and who is away.", "Risk: data requests near 30 days and any breach not yet reported."]),
    S("approvals", "Approvals waiting for you", intro="Budget requests (My Space → Budget Requests) and leave (HR → Approvals). Approving your own item is blocked for everyone except the CEO."),
    S("briefing", "Your morning briefing", intro="Each morning the assistant sends the COO and CEO a short briefing: revenue against target, pipeline, leads waiting, overdue follow-ups, departures this week and overdue invoices. Times are set under Sales → Assistant Settings."),
])


def apply(manuals):
    for m in manuals:
        if m.slug in RICH:
            m.shot_user, m.rich = RICH[m.slug][0], RICH[m.slug][1]
