"""In-app manuals. Plain data so they can be edited without touching views; each manual belongs to a module and is shown only to people who can use it."""
from dataclasses import dataclass, field


@dataclass
class Section:
    slug: str
    title: str
    menu: str = ""            # e.g. "Front Desk → New Lead"
    who: str = "Everyone"
    intro: str = ""
    bullets: list = field(default_factory=list)
    steps: list = field(default_factory=list)
    tip: str = ""
    shot: str = ""            # screenshot file name (without .png) under static/img/manual/<manual>/
    shot_path: str = ""       # app path the screenshot is taken from (used by capture_manual_shots)


@dataclass
class Manual:
    slug: str
    title: str
    module: str                    # who sees it: anyone with access to this module ("" = everyone)
    summary: str
    sections: list = field(default_factory=list)       # [(heading, [paragraph or "- step", ...])]
    executive: bool = False        # CEO / COO / Admin only
    rich: list = field(default_factory=list)           # [Section]: used instead of `sections` when present
    shot_user: str = ""            # demo login used to take the screenshots

    def all_sections(self):
        if self.rich:
            return self.rich
        out = []
        for i, (heading, lines) in enumerate(self.sections):
            paras = [l for l in lines if not l.startswith("- ")]
            steps = [l[2:] for l in lines if l.startswith("- ")]
            out.append(Section(slug=f"s{i + 1}", title=heading, intro=paras[0] if paras else "", bullets=paras[1:], steps=steps))
        return out


MANUALS = [
    Manual("getting-started", "Getting started", "", "Signing in, your menu, notifications, security and installing the app on your phone.", [
        ("Sign in", ["Open the system address and sign in with your work e-mail and password.", "If your role requires two-step sign-in you will be asked for a 6-digit code from your authenticator app the first time and every time after.",
                     "Forgot your password? Ask Admin to reset it. You can change it any time under My Space → Security."]),
        ("Your menu", ["The left menu shows only what your role can use. On a phone, the bottom bar has your main screens and More opens the rest.", "My Space holds things that belong to you: To-Do, Calendar, Leave, Reports and Security."]),
        ("Stay safe", ["Turn on two-step sign-in under My Space → Security and keep your recovery codes somewhere safe, not on the same phone.", "Never share your password. Everything you do is recorded in the system log under your name."]),
        ("Install on your phone", ["Open the system in your phone's browser, then use Install on your phone (Administration menu) for the exact steps for Android and iPhone."]),
        ("Ask the assistant", ["The round chat button at the bottom-right answers questions about the clients, enquiries, follow-ups and packages you are allowed to see. It cannot change anything."]),
    ]),
    Manual("my-space", "My Space: To-Do, Calendar, Leave", "", "Your private to-do list, calendar invitations and leave requests.", [
        ("To-Do", ["My To-Do is private: nobody, not your manager, the CEO or Admin, can see it.", "- Type an item, optionally add a due date, press Add.", "- Tick an item when done; Undo brings it back."]),
        ("Calendar", ["My Calendar shows your events and your department's. Add an event, set the time, then invite people.", "Inviting someone senior to you needs their acceptance; inviting someone at or below your level puts it straight on their calendar.",
                      "Front Desk can book the CEO's calendar directly. Approved leave appears automatically."]),
        ("Leave", ["Open My Leave. Your balance for each leave type is worked out from approved leave, so it is always current.", "- Choose the type and dates. Weekends and public holidays are not counted.", "- Sick, maternity and paternity leave need a supporting document.",
                   "- Your manager is notified. You are told when it is approved or declined.", "Pending leave can be withdrawn at once. Approved leave needs your manager to confirm a cancellation."]),
    ]),
    Manual("sales", "Sales: enquiries to bookings", "sales", "Pipeline, follow-ups, quotations, itineraries, e-signature and payment plans.", [
        ("Work an enquiry", ["Open Pipeline to see every lead by stage. Tick several cards to move them together.", "Log each call straight after you make it. A busy or unanswered call automatically creates a call-back follow-up two hours later.",
                             "Response time counts: the first contact should happen within the SLA shown on your dashboard."]),
        ("Your QR business card", ["Open Sales → My QR Card. Download the card or the QR code, or share the link on WhatsApp.",
                                   "Whoever scans it sees your card, can save your contact, and can send a trip request. The request becomes your lead straight away and you get an alert. The page counts scans, saved contacts, leads and bookings.",
                                   "Managers, the COO and the director see every agent's card under Sales → Agent Cards."]),
        ("Travel day", ["On a booking press Travel day: one page with the client and party, departure (guide, driver, vehicle, pick-up), flights, lodges, documents and payments. Add flights there; the client sees them on their trip page."]),
        ("Quote and book", ["From an enquiry choose New quotation, add lines, then Send. The client sees the total in their currency.", "When the client accepts, click Accept to create a booking with the deposit due.",
                            "Use Send for e-signature on a quotation or booking: the client gets a link, types their name and draws a signature on their phone."]),
        ("Deposits and instalments", ["Record payments on the booking. If your company has switched on KYC before deposit, verify the client's ID first (Deal Desk → Client KYC); a different person from the client's owner must verify.",
                                      "For long lead times use Payment plan on the booking: choose the number of instalments and the first due date. Payments settle the oldest instalment first and the client gets an SMS before each is due."]),
        ("Build an itinerary", ["Open Itineraries, add days, destinations, stays and activities. Print or download the PDF for the client."]),
    ]),
    Manual("frontdesk", "Front Desk", "frontdesk", "Capture leads, assign them to agents, run the visitor and equipment registers and keep the desk calendar.", [], rich=[
        Section("intro", "Welcome", intro="This is your guide to the Front Desk area of Pocket of Paradise Travel Company: where every walk-in, WhatsApp message, call and DM first becomes a lead, and where visitors, appointments and equipment are tracked through the day.",
                bullets=["Capture a new lead the moment someone enquires, and either assign it to an agent on the spot or send it to the queue.", "Keep an eye on who is waiting for an agent, so no enquiry sits unanswered.",
                         "Sign visitors in and out, log appointments, and track equipment such as radios, cooler boxes and cameras leaving and returning.", "Leave a shift note so the next person at the desk knows what is open."]),
        Section("dashboard", "Front Desk Dashboard", "Front Desk → Front Desk Dashboard", "Everyone",
                "Your home screen: a snapshot of today, showing how many leads came in by channel, who is waiting for an agent, and which agents have room to take more.",
                bullets=["Four channel tiles (WhatsApp, Walk-in, Call, DM) with today's count. The + on each tile opens New Lead with that channel already chosen.", "Leads today, leads not yet assigned, quotes in progress and agents available.",
                         "Sales agents today: who is in, how many leads they have had against their daily limit, how many are open, and their specialties.", "Waiting to be assigned: the oldest leads first, each with a suggested agent. Press Assign to accept the suggestion or pick someone else from the list."],
                tip="The suggestion follows the same rules as automatic assignment: the client's existing agent first, then any matching rule, then whoever has waited longest. You can always overrule it.", shot="dashboard", shot_path="/frontdesk/"),
        Section("new-lead", "Capturing a New Lead", "Front Desk → New Lead", "Everyone", "Every enquiry that comes through the desk (in person, on WhatsApp, by phone or by DM) is captured here first.",
                steps=["Pick the source: WhatsApp, Walk-in, Call or DM.", "Enter the client's name and phone number. As you type the number, the system checks whether they are a returning client and shows you their history if so.",
                       "Choose the package they are asking about, or describe their interest in your own words (for example, Mara 3 days in August).", "Add what you know: purpose, budget, preferred travel date, number of adults and children, and any notes.",
                       "Choose an agent from the list on the right (greyed-out agents are unavailable or at their daily limit). Leave it empty to let the system pick.", "Press Save & Assign to hand the lead over now, or Save, assign later to leave it in the waiting list."],
                tip="Tick Add another lead after saving when you are capturing several enquiries back to back. The channel stays selected and the form clears. The two Notify options alert the agent and send the client a WhatsApp confirmation.", shot="new-lead", shot_path="/frontdesk/new-lead/"),
        Section("leads-log", "Leads Log", "Front Desk → Leads Log", "Everyone", "A running log of every lead captured at the desk, showing who it is assigned to and when it came in.",
                bullets=["Tabs for All channels, WhatsApp, Walk-in, Call and DM, each showing how many leads are still waiting for an agent.", "Search by reference, client, phone or package, and filter by status, date range and agent.",
                         "Change the agent from the dropdown on a row. The row updates straight away and the agent is told.", "Remind agent nudges an agent whose lead has not been contacted yet.", "Export the current view to CSV or Excel."],
                shot="leads-log", shot_path="/frontdesk/queues/?period=30d"),
        Section("visitors", "Visitors & Appointments", "Front Desk → Visitors & Appointments", "Everyone", "Everything else that happens at the desk in a day: visitor sign-in and sign-out, scheduled appointments, the equipment register and shift notes.",
                steps=["Sign a visitor in with their name, reason and who they have come to see. The host is notified. Sign them out when they leave.", "Book an appointment with a title, time and host, and change its status as the day goes on.",
                       "Issue equipment to a person with a due-back date, and mark it Returned when it comes back. Anything overdue is flagged at the top.", "Add a shift note for whoever is on next."], shot="visitors", shot_path="/frontdesk/visitors/"),
        Section("cards", "Business Cards", "Front Desk → Business Cards", "Everyone", "Log every business card handed to the desk: name, e-mail, number and what kind of contact it is, so nobody's details get lost in a drawer.",
                steps=["Press New card. A pop-up opens.", "Enter the name, e-mail and/or phone number (at least one), choose the category (supplier, tour operator, client, corporate, partner, media, government or other) and add company and job title.", "Drop a photo of the card into the box if you want to keep it.", "Save. Use the category tabs and the search box to find someone later, or export the list."],
                tip="Cards are available as an audience for the ICT team's bulk SMS and e-mail. Tick \"Do not send bulk messages\" on a card if the person asks not to be contacted.", shot="cards", shot_path="/frontdesk/cards/"),
        Section("calendar", "Your Calendar", "Front Desk → Calendar", "Everyone", "The Front Desk team's shared calendar for meetings, reminders and anything worth flagging for the whole desk, separate from individual appointments.",
                bullets=["Add an event with a time, place and notes, and invite colleagues. Inviting someone senior to you waits for their acceptance.", "Front Desk can book the CEO's calendar directly with Book the CEO."], shot="calendar", shot_path="/frontdesk/calendar/"),
        Section("reports", "Reports", "Front Desk → Reports", "Everyone", "Leads captured, where they came from and how well they are being followed up, for the day, the week or the month.",
                bullets=["Daily, Weekly and Monthly views, compared with the previous period.", "Leads by source (contacted and not yet), leads per week for the last eight weeks, and each agent's contact rate.", "Export PDF for the meeting. The Reports page under My Space also lets you schedule reports by e-mail."], shot="reports", shot_path="/frontdesk/report/"),
        Section("tips", "Good habits", intro="Small things that keep the desk running smoothly.", bullets=["Assign within 15 minutes. The Reports page shows your average.", "Always check the returning-client message before creating a new record.",
                                                                            "Never take card numbers or PINs over WhatsApp or the phone.", "Finish every shift with a shift note."]),
    ], shot_user="deskmanager"),
    Manual("trips", "Trips and operations", "trips", "Departures, manifests, vehicles, drivers and guides.", [
        ("Departures", ["Create a departure from a package, attach confirmed bookings, then assign a vehicle, driver and guide. The system warns about double bookings and expired licences.", "Mark a departure Completed when the trip ends: revenue is recognised on completion."]),
        ("On the road", ["Drivers and guides can check in from the phone; check-ins are saved offline and sent when the signal returns."]),
    ]),
    Manual("finance", "Finance", "finance", "Invoices, receipts, bills, payroll, fixed assets, budgets and statements.", [
        ("Money in", ["Invoices are created from bookings. Record receipts against them; the ledger posts automatically and cannot be edited, only reversed."]),
        ("Money out", ["Enter supplier bills, approve them, then pay in a payment run. Raise a Budget Request (My Space) for spend that needs approval."]),
        ("Fixed assets", ["Add assets with cost, residual value and life. Depreciation posts on the 1st of each month for the month before; running it twice does nothing."]),
        ("Reports", ["Open Reports for outstanding invoices, the asset register and more. Every report can be exported to Excel, CSV or PDF and scheduled by e-mail."]),
    ]),
    Manual("marketing", "Marketing", "marketing", "Campaigns, posts, the unified inbox, link tracking and lead assignment.", [
        ("Capture leads", ["Share the public capture link at expos; every lead is attributed to the event and given to an agent automatically.", "The public catalogue at /explore/ lets visitors browse packages and send an enquiry; it feeds the same pipeline."]),
        ("Track what works", ["Use tracked links on each channel and read the Analytics page to see which source produces bookings, not just enquiries."]),
    ]),
    Manual("hr", "HR: leave approvals and policy", "hr", "Approving leave, the policy and public holidays.", [
        ("Approve leave", ["Open Approvals. Approve or decline with a note. Nobody can approve their own leave, except the CEO who has no one above them.", "A cancellation request on approved leave shows as a separate line to confirm."]),
        ("Policy", ["Policy & Holidays sets the days per leave type, the carry-forward cap and the public-holiday list. Confirm each year's gazetted dates, for example Eid, as they are announced late."]),
    ]),
    Manual("deals", "Deal Desk", "deals", "KYC, signatures and payment plans in one place.", [
        ("KYC", ["Submit the client's ID number and a scan. A manager verifies it. The rule that blocks deposits for unverified clients is switched on under Deal Desk → Overview."]),
        ("Signatures", ["All sent links are listed with their state. Copy the link to send it on WhatsApp. Links expire after 14 days."]),
    ]),
    Manual("ict", "ICT: WhatsApp bot and bulk messages", "ict", "Chats, take-over, bulk SMS and e-mail, settings and analysis.", [
        ("Bulk SMS and e-mail", ["Open Bulk SMS & Email, then New SMS or New e-mail. SMS is sent through Africa's Talking and e-mail through Brevo.", "- Write the message. Use {first_name}, {name} or {company} to personalise it. SMS shows how many characters and messages each recipient will get.",
                                 "- Choose who receives it: clients (by the channel they came through, their stage and market), business cards by category, or numbers or addresses you paste. The recipient count updates as you choose.", "- Send a test to yourself first.", "- Press Send now, or schedule it. You are asked to confirm the number of recipients.",
                                 "Opted-out people and invalid numbers or addresses are skipped automatically and listed on the broadcast. Every e-mail carries an unsubscribe link. Until the keys are saved under Integration Keys, sending is simulated and nothing leaves the system."]),
        ("Chats", ["The bot answers new WhatsApp messages using only the published catalogue and never invents prices. It captures a lead once it has a name and interest, and hands over when the customer asks for a person, is upset, or wants to pay.",
                   "Open a chat and press Take over to reply yourself, then Resume bot to hand it back."]),
        ("Connect WhatsApp", ["Open ICT → WhatsApp. Paste the access token, phone number ID, WhatsApp Business Account ID and app secret from the Meta app, then Save. Everything is stored encrypted and used from the next message.",
                              "- Press Generate next to Verify token, copy the Callback URL and the token into Meta (WhatsApp → Configuration → Webhook), press Verify and save, and subscribe to “messages”.",
                              "- Press Test connection, then Subscribe to messages.",
                              "- Press Add the standard templates, then Submit all drafts to Meta. When Meta approves them (minutes to a day), press Check status with Meta.",
                              "- Send a test message to your own number.",
                              "WhatsApp only allows free text within 24 hours of a person's last message. Outside that window the system sends the approved template for that kind of message instead. The ICT dashboard's go-live checklist shows what is left."]),
        ("Alerts on WhatsApp", ["Every alert anyone receives in the app (new leads, approvals, reminders, overdue items) also goes to their WhatsApp, e-mail or both, as each person chose on My Profile → Alerts. A pop-up appears in the app when a new alert arrives.",
                                "ICT → WhatsApp → Message log lists every message with its status (sent, delivered, read, failed). Failed WhatsApp messages are retried automatically; you can also retry them by hand."]),
        ("Travel reminders", ["Days before each deposit-paid or confirmed trip (5 by default; add more such as 5,1 under ICT → WhatsApp), the agent gets a WhatsApp with a link to the travel-day preview, and the client gets one with what to have ready and a link to their own trip page (no login needed)."]),
        ("AI and the business assistant", ["Open ICT → AI. Choose Claude or OpenAI, paste the key, pick the model and press Test connection. Switch provider at any time.",
                                           "The director and COO can text the company WhatsApp number and ask about leads, sales, agents, departures and revenue. Only numbers on the profiles of those roles get business answers; every other number reaches the customer bot. Questions, answers, usage and estimated cost are listed on the same page."]),
        ("Set-up", ["Save the AI key and the WhatsApp token under Administration → Integration Keys and use Test key to check them. In Bot Settings tick Bot replies automatically.", "The safety rules are fixed in the system and cannot be changed from screen."]),
    ]),
    Manual("compliance", "Data protection", "compliance", "Privacy requests, breaches and retention.", [
        ("Requests", ["Log every request from a person to see, correct or delete their data. The 30-day deadline is tracked and alerts are sent.", "Deleting anonymises the person's record; financial records are kept as the law requires."]),
        ("Breaches", ["Record any breach straight away. Serious ones must be reported to the regulator within 72 hours."]),
    ]),
    Manual("admin", "Administration", "admin", "Users, roles, integration keys, logs and imports.", [
        ("Users and roles", ["Create users under Users & Roles. Roles & Access shows who holds each role; drag a person between roles to change it. Every change is logged."]),
        ("Integration keys", ["Keys are stored encrypted and shown masked. Use Test key after saving: it tells you whether the key is valid, unauthorised or out of credit."]),
        ("Import data", ["Download the template, fill it, upload. Check the preview, then commit. Every import can be undone as one batch."]),
        ("System logs", ["Every sign-in, change and key update is recorded with who, when and from where."]),
    ]),
    Manual("executive-guide", "Executive guide (CEO / COO)", "", "What each view means, what you can and cannot do, and the five checks to make each week.", [
        ("Your access", ["The CEO and COO can open every department. The CEO's access is read-only by design: you see everything and approve things, but data is changed by the team. The COO acts as head of every department.",
                         "Roles & Access (Administration) shows who holds which role. System Logs shows every action by person and time."]),
        ("What to check each week", ["1. Sales: the pipeline by stage and the agent-performance report; look for leads stuck in Quoted.", "2. Money: outstanding invoices older than 14 days and the budget requests waiting on you.",
                                     "3. Trips: next fortnight's departures have a vehicle, driver and guide.", "4. People: leave approvals waiting and who is away this week.", "5. Risk: open data requests nearing 30 days, and any breach not yet reported."]),
        ("Approvals waiting for you", ["Budget requests (My Space → Budget Requests) and leave (HR → Approvals). Approving your own item is blocked for everyone except the CEO."]),
        ("Reports", ["Reports gathers every report you can see. Schedule the ones you want by e-mail every Monday or on the 1st of the month."]),
        ("Calendar", ["Staff can invite you; an invitation from someone junior waits for your acceptance. Front Desk can book your time directly."]),
        ("AI features", ["The WhatsApp bot and the in-app assistant need an AI key under Integration Keys. Without one they politely hand over to a person, so nothing breaks."]),
    ], executive=True),
]
from . import rich as _rich  # noqa: E402

_rich.apply(MANUALS)
BY_SLUG = {m.slug: m for m in MANUALS}


def visible(user):
    from apps.accounts import roles as R
    from apps.core.modules import user_can_access_module

    out = []
    for m in MANUALS:
        if m.executive and not (user.is_superuser or user.has_role(*R.ALL_ACCESS)):
            continue
        if m.module and not user_can_access_module(user, m.module):
            continue
        out.append(m)
    return out
