"""Fill the database with realistic Pocket of Paradise Travel Company demo data (Phase 1 scope).

    python manage.py seed_demo            # only seeds an empty database
    python manage.py seed_demo --reset    # wipe demo tables first, then seed
"""
import random
from datetime import date, datetime, time, timedelta
from decimal import Decimal

from django.contrib.auth.models import Group
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from apps.accounts import roles as R
from apps.accounts.models import User
from apps.catalog.models import Accommodation, Destination, Itinerary, ItineraryDay, RoomRate, TourPackage
from apps.contacts.models import Client, TravelPartyMember
from apps.core.models import CompanySetting, Currency, ExchangeRate, Sequence
from apps.fleet.models import FuelLog, Vehicle
from apps.leads.models import Enquiry, LeadActivity, LeadAssignment, QuotationStatus, Stage
from apps.notifications.models import Notification
from apps.people.models import Department, Employee, Specialty
from apps.sales import services as sales_services
from apps.sales.models import (AssignmentRule, Booking, BookingPayment, BookingStatus, CallLog, FollowUp, Quotation,
                               QuotationItem, QuotationStatus as QStatus, SalesSettings, SalesTarget)

DEMO_PASSWORD = "Safari@2025"
DOMAIN = "pocketofparadise.co.ke"

SPECIALTIES = ["Maasai Mara", "Amboseli", "Coastal beach", "Mt Kenya trekking", "Northern circuit", "Group tours",
               "Honeymoon / luxury", "Diaspora clients"]

# name, email-local, title, department, roles, flags, specialties, languages
# A lead's "next step" must make sense for where it is in the pipeline
STAGE_NEXT = {
    Stage.ENQUIRY: ["Make first contact", "Send quotation", "Confirm travel dates", "Follow up on WhatsApp"],
    Stage.QUOTED: ["Follow up on the quotation", "Confirm travel dates", "Answer questions on the quote"],
    Stage.HOT: ["Ask for a decision", "Send itinerary", "Negotiate and close"],
    Stage.ITINERARY: ["Follow up on the itinerary", "Ask for feedback", "Ask for the deposit"],
    Stage.DEPOSIT: ["Chase balance", "Confirm travel documents"],
}

STAFF = [
    ("Anne Wairimu", "admin", "Office Administrator", "Administration", [R.ADMIN], {}, [], "English, Swahili"),
    ("Samuel Otieno", "director", "Managing Director (CEO)", "Management", [R.DIRECTOR], {}, [], "English, Swahili"),
    ("Michael Oduya", "coo", "Chief Operating Officer", "Management", [R.COO], {}, [], "English, Swahili"),
    ("Ian Mwiti", "ict", "ICT Officer", "Administration", [R.ICT_OFFICER], {}, [], "English, Swahili"),
    ("Faith Chebet", "finance", "Finance Officer", "Finance", [R.FINANCE], {}, [], "English, Swahili"),
    ("Joyce Nduta", "salesmanager", "Sales Manager", "Sales", [R.SALES_MANAGER], {}, [], "English, Swahili"),
    ("Sarah Njeri", "sarah", "Senior Travel Consultant", "Sales", [R.SALES_AGENT], {"is_sales_agent": True},
     ["Maasai Mara", "Honeymoon / luxury"], "English, Swahili, French"),
    ("James Kariuki", "james", "Travel Consultant", "Sales", [R.SALES_AGENT], {"is_sales_agent": True},
     ["Mt Kenya trekking", "Northern circuit"], "English, Swahili"),
    ("Grace Mumbi", "grace", "Travel Consultant", "Sales", [R.SALES_AGENT], {"is_sales_agent": True},
     ["Maasai Mara", "Amboseli"], "English, Swahili, German"),
    ("David Mwangi", "david", "Travel Consultant", "Sales", [R.SALES_AGENT], {"is_sales_agent": True, "on_duty": "off"},
     ["Group tours"], "English, Swahili"),
    ("Mercy Atieno", "mercy", "Travel Consultant (International)", "Sales", [R.SALES_AGENT],
     {"is_sales_agent": True}, ["Diaspora clients", "Group tours"], "English, Swahili, Italian"),
    ("Peter Odhiambo", "peter", "Travel Consultant", "Sales", [R.SALES_AGENT], {"is_sales_agent": True},
     ["Coastal beach"], "English, Swahili"),
    ("Rose Mwende", "deskmanager", "Front Desk Manager", "Front Desk", [R.FRONTDESK_MANAGER], {}, [],
     "English, Swahili"),
    ("Lucy Achieng", "frontdesk", "Reservations Officer", "Front Desk", [R.RESERVATIONS], {}, [], "English, Swahili"),
    ("Cynthia Awuor", "reception", "Receptionist", "Front Desk", [R.RECEPTIONIST], {}, [], "English, Swahili"),
    ("Hassan Omar", "dispatch", "Dispatch Coordinator", "Front Desk", [R.DISPATCHER], {}, [], "English, Swahili, Arabic"),
    ("Esther Wanjiku", "marketing", "Marketing Manager", "Marketing", [R.MARKETING], {}, [], "English, Swahili"),
    ("Kelvin Sila", "production", "Trip Production Lead (shooting)", "Marketing", [R.PRODUCTION_LEAD], {}, [],
     "English, Swahili"),
    ("Tony Mutua", "tony", "Content Shooter", "Marketing", [R.PRODUCTION_STAFF], {}, [], "English, Swahili"),
    ("Diana Kerubo", "diana", "Video Editor", "Marketing", [R.PRODUCTION_STAFF], {}, [], "English, Swahili"),
    ("Brian Kamau", "operations", "Operations Coordinator", "Operations", [R.OPERATIONS], {}, [], "English, Swahili"),
    ("Nancy Wafula", "suppliers", "Supplier Coordinator", "Operations", [R.SUPPLIER_COORDINATOR], {}, [], "English, Swahili"),
    ("John Lekishon", "john", "Senior Safari Guide", "Operations", [R.GUIDE], {"is_guide": True, "is_driver": True},
     ["Maasai Mara", "Amboseli"], "English, Swahili, Maa"),
    ("Daniel Kiptoo", "daniel", "Safari Guide / Driver", "Operations", [R.GUIDE],
     {"is_guide": True, "is_driver": True, "on_duty": "out_on_trip"}, ["Northern circuit"], "English, Swahili"),
    ("Ali Bakari", None, "Driver Guide (Coast)", "Operations", [], {"is_guide": True, "is_driver": True},
     ["Coastal beach"], "English, Swahili, Arabic"),
    ("Moses Njoroge", None, "Driver", "Operations", [], {"is_driver": True}, [], "English, Swahili"),
]

DESTINATIONS = [
    # name, region, country, lat, lng, best time, activities, summary, featured
    ("Maasai Mara", "mara", "KE", -1.4061, 35.0086, "Jul – Oct (Great Migration)",
     "Game drives, Hot-air balloon, Maasai village visit, Bush breakfast",
     "Kenya's most famous reserve: big cats, the Great Migration and endless savanna.", True),
    ("Amboseli", "amboseli", "KE", -2.6527, 37.2606, "Jun – Oct, Jan – Feb",
     "Game drives, Elephant tracking, Kilimanjaro views, Observation Hill",
     "Big-tusker elephants with Mt Kilimanjaro rising behind them.", True),
    ("Lake Nakuru", "nakuru", "KE", -0.3667, 36.0833, "All year", "Game drives, Rhino tracking, Flamingo viewing",
     "Rift Valley lake with black and white rhino and flocks of flamingo.", False),
    ("Tsavo East", "tsavo", "KE", -2.9833, 38.4667, "Jun – Oct", "Game drives, Red elephants, Lugard Falls",
     "Vast wilderness famous for red-dust elephants.", False),
    ("Tsavo West", "tsavo", "KE", -3.3500, 38.1000, "Jun – Oct", "Mzima Springs, Shetani lava flow, Game drives",
     "Volcanic landscapes, springs and the Ngulia rhino sanctuary.", False),
    ("Samburu", "samburu", "KE", 0.6167, 37.5333, "Jun – Oct, Dec – Mar",
     "Game drives, Samburu Special Five, Cultural visit", "Arid north with reticulated giraffe and Grevy's zebra.", False),
    ("Diani Beach", "coast", "KE", -4.3167, 39.5667, "Dec – Mar, Jul – Oct",
     "Snorkelling, Dhow cruise, Kite surfing, Colobus trust", "White-sand Indian Ocean beach south of Mombasa.", True),
    ("Mt Kenya", "mtkenya", "KE", -0.1521, 37.3084, "Jan – Feb, Aug – Sep", "Trekking, Point Lenana summit, Camping",
     "Africa's second-highest mountain; Sirimon and Chogoria routes.", False),
    ("Lake Naivasha", "naivasha", "KE", -0.7667, 36.4167, "All year",
     "Boat ride, Crescent Island walk, Hell's Gate cycling", "Freshwater lake with hippos and walking safaris.", False),
    ("Nairobi National Park", "nairobi", "KE", -1.3733, 36.8589, "All year",
     "Game drive, Giraffe Centre, Sheldrick elephant orphanage", "Wildlife against the city skyline, 20 minutes away.", False),
    ("Serengeti", "crossborder", "TZ", -2.3333, 34.8333, "Dec – Mar calving, Jun – Jul river crossings",
     "Game drives, Balloon safari, Migration crossings", "Tanzania add-on continuing the Mara migration story.", False),
    ("Bwindi Impenetrable Forest", "crossborder", "UG", -1.0333, 29.7000, "Jun – Aug, Dec – Feb",
     "Gorilla trekking, Batwa experience, Forest walks", "Mountain gorilla trekking in south-west Uganda.", False),
]

ACCOMMODATIONS = [
    ("Mara Serena Safari Lodge", "Maasai Mara", "lodge", 5), ("Basecamp Mara Tented Camp", "Maasai Mara", "camp", 4),
    ("Ol Tukai Lodge", "Amboseli", "lodge", 4), ("Kibo Safari Camp", "Amboseli", "camp", 3),
    ("Lake Nakuru Sopa Lodge", "Lake Nakuru", "lodge", 4), ("Voi Safari Lodge", "Tsavo East", "lodge", 3),
    ("Kilaguni Serena", "Tsavo West", "lodge", 4), ("Samburu Sopa Lodge", "Samburu", "lodge", 4),
    ("Leopard Beach Resort", "Diani Beach", "hotel", 5), ("Diani Homestay Cottages", "Diani Beach", "homestay", 3),
    ("Naivasha Sopa Resort", "Lake Naivasha", "hotel", 4), ("Serengeti Sopa Lodge", "Serengeti", "lodge", 4),
    ("Buhoma Community Lodge", "Bwindi Impenetrable Forest", "lodge", 3),
]

# title, type, days, nights, currency, price, supplement, child, status, destinations, difficulty, day plans
PACKAGES = [
    ("3-Day Maasai Mara Safari", "group", 3, 2, "KES", 48500, 9500, 24000, "published", ["Maasai Mara"], "easy",
     [("Nairobi to Maasai Mara", "Maasai Mara", "Basecamp Mara Tented Camp", "LD", 270, 5.5, "Afternoon game drive"),
      ("Full day in the Mara", "Maasai Mara", "Basecamp Mara Tented Camp", "BLD", None, None,
       "Full-day game drive, Picnic lunch by the Mara River"),
      ("Mara to Nairobi", "Nairobi National Park", None, "B", 270, 5.5, "Morning game drive")]),
    ("4-Day Mara & Nakuru", "private", 4, 3, "USD", 890, 180, 450, "published", ["Maasai Mara", "Lake Nakuru"], "easy",
     [("Nairobi to Maasai Mara", "Maasai Mara", "Mara Serena Safari Lodge", "LD", 270, 5.5, "Evening game drive"),
      ("Maasai Mara full day", "Maasai Mara", "Mara Serena Safari Lodge", "BLD", None, None, "Game drives, Maasai village visit"),
      ("Mara to Lake Nakuru", "Lake Nakuru", "Lake Nakuru Sopa Lodge", "BLD", 240, 5, "Afternoon rhino tracking"),
      ("Nakuru to Nairobi", "Nairobi National Park", None, "B", 160, 3, "Flamingo viewing")]),
    ("Amboseli 2-Day Kilimanjaro Views", "group", 2, 1, "KES", 32500, 6000, 16000, "published", ["Amboseli"], "easy",
     [("Nairobi to Amboseli", "Amboseli", "Kibo Safari Camp", "LD", 240, 4.5, "Afternoon game drive"),
      ("Sunrise drive & return", "Nairobi National Park", None, "B", 240, 4.5, "Sunrise game drive, Observation Hill")]),
    ("7-Day Kenya Classic", "private", 7, 6, "USD", 2450, 520, 1200, "published",
     ["Samburu", "Lake Nakuru", "Maasai Mara", "Lake Naivasha"], "moderate",
     [("Nairobi to Samburu", "Samburu", "Samburu Sopa Lodge", "LD", 340, 6, "Afternoon game drive"),
      ("Samburu full day", "Samburu", "Samburu Sopa Lodge", "BLD", None, None, "Samburu Special Five, Cultural visit"),
      ("Samburu to Lake Nakuru", "Lake Nakuru", "Lake Nakuru Sopa Lodge", "BLD", 300, 6, "Rhino tracking"),
      ("Nakuru to Maasai Mara", "Maasai Mara", "Mara Serena Safari Lodge", "BLD", 240, 5, "Evening game drive"),
      ("Mara full day", "Maasai Mara", "Mara Serena Safari Lodge", "BLD", None, None, "Hot-air balloon, Game drives"),
      ("Mara to Naivasha", "Lake Naivasha", "Naivasha Sopa Resort", "BLD", 200, 4.5, "Boat ride"),
      ("Naivasha to Nairobi", "Nairobi National Park", None, "B", 90, 2, "Crescent Island walk")]),
    ("Diani Beach Escape 5 Days", "private", 5, 4, "KES", 68000, 22000, 30000, "seasonal", ["Diani Beach"], "easy",
     [("Arrive Ukunda, transfer to Diani", "Diani Beach", "Leopard Beach Resort", "D", 15, 0.5, "Beach sundowner"),
      ("Kisite marine park", "Diani Beach", "Leopard Beach Resort", "BLD", None, None, "Snorkelling, Dhow cruise"),
      ("Leisure day", "Diani Beach", "Leopard Beach Resort", "BD", None, None, "Kite surfing, Colobus trust"),
      ("Shimba Hills day trip", "Diani Beach", "Leopard Beach Resort", "BLD", 60, 1.5, "Forest walk"),
      ("Departure", "Diani Beach", None, "B", 15, 0.5, "")]),
    ("Tsavo East & West 4 Days", "private", 4, 3, "USD", 1180, 240, 590, "published", ["Tsavo East", "Tsavo West"], "easy",
     [("Mombasa to Tsavo East", "Tsavo East", "Voi Safari Lodge", "LD", 160, 3, "Afternoon game drive"),
      ("Tsavo East full day", "Tsavo East", "Voi Safari Lodge", "BLD", None, None, "Lugard Falls, Red elephants"),
      ("To Tsavo West", "Tsavo West", "Kilaguni Serena", "BLD", 120, 3, "Mzima Springs"),
      ("Return to Mombasa", "Diani Beach", None, "B", 240, 5, "Shetani lava flow")]),
    ("Mt Kenya Sirimon–Chogoria Trek", "group", 5, 4, "KES", 58000, 8000, 0, "published", ["Mt Kenya"], "challenging",
     [("Nairobi to Old Moses Camp", "Mt Kenya", None, "LD", 230, 4, "Acclimatisation walk"),
      ("Old Moses to Shipton's", "Mt Kenya", None, "BLD", 14, 7, "Trekking"),
      ("Summit Point Lenana", "Mt Kenya", None, "BLD", 12, 9, "Point Lenana summit"),
      ("Descend to Chogoria", "Mt Kenya", None, "BLD", 18, 7, "Gorges valley"),
      ("Return to Nairobi", "Nairobi National Park", None, "B", 200, 4, "")]),
    ("Nairobi Day Trip: Park & Giraffes", "day", 1, 0, "KES", 9500, 0, 5500, "published", ["Nairobi National Park"], "easy",
     [("Nairobi highlights", "Nairobi National Park", None, "L", 40, 2,
       "Game drive, Giraffe Centre, Sheldrick elephant orphanage")]),
    ("Mara & Serengeti Migration 8 Days", "custom", 8, 7, "USD", 4200, 900, 2100, "seasonal",
     ["Maasai Mara", "Serengeti"], "moderate",
     [("Nairobi to Maasai Mara", "Maasai Mara", "Mara Serena Safari Lodge", "LD", 270, 5.5, "Game drive"),
      ("Mara river crossings", "Maasai Mara", "Mara Serena Safari Lodge", "BLD", None, None, "Migration crossings"),
      ("Mara full day", "Maasai Mara", "Basecamp Mara Tented Camp", "BLD", None, None, "Balloon safari"),
      ("Cross to Serengeti via Isebania", "Serengeti", "Serengeti Sopa Lodge", "BLD", 250, 6, "Border crossing"),
      ("Northern Serengeti", "Serengeti", "Serengeti Sopa Lodge", "BLD", None, None, "Game drives"),
      ("Central Serengeti", "Serengeti", "Serengeti Sopa Lodge", "BLD", None, None, "Seronera valley"),
      ("Return to Mara", "Maasai Mara", "Basecamp Mara Tented Camp", "BLD", 250, 6, ""),
      ("Fly to Nairobi", "Nairobi National Park", None, "B", None, 1, "Scheduled flight")]),
    ("Bwindi Gorilla Trek 4 Days", "custom", 4, 3, "USD", 2850, 400, 0, "draft", ["Bwindi Impenetrable Forest"], "challenging",
     [("Fly Entebbe to Kihihi", "Bwindi Impenetrable Forest", "Buhoma Community Lodge", "LD", 40, 1.5, ""),
      ("Gorilla trekking", "Bwindi Impenetrable Forest", "Buhoma Community Lodge", "BLD", None, None, "Gorilla trekking"),
      ("Batwa experience", "Bwindi Impenetrable Forest", "Buhoma Community Lodge", "BLD", None, None, "Batwa experience"),
      ("Return to Entebbe", "Bwindi Impenetrable Forest", None, "B", None, 1.5, "")]),
    ("Honeymoon Mara & Diani 7 Days", "private", 7, 6, "USD", 3650, 0, 0, "published", ["Maasai Mara", "Diani Beach"], "easy",
     [("Fly to Maasai Mara", "Maasai Mara", "Mara Serena Safari Lodge", "LD", None, 1, "Sundowner"),
      ("Balloon & bush breakfast", "Maasai Mara", "Mara Serena Safari Lodge", "BLD", None, None, "Hot-air balloon, Bush breakfast"),
      ("Mara game drives", "Maasai Mara", "Mara Serena Safari Lodge", "BLD", None, None, "Game drives"),
      ("Fly to Diani", "Diani Beach", "Leopard Beach Resort", "BLD", None, 2, ""),
      ("Beach day", "Diani Beach", "Leopard Beach Resort", "BLD", None, None, "Couples spa"),
      ("Dhow sunset cruise", "Diani Beach", "Leopard Beach Resort", "BLD", None, None, "Dhow cruise"),
      ("Departure", "Diani Beach", None, "B", None, None, "")]),
]

VEHICLES = [
    ("KDA 214M", "Simba", "van", "Toyota Hiace 4x4 pop-top", 2019, 7, "available", "John Lekishon"),
    ("KDB 551Q", "Twiga", "cruiser", "Toyota Land Cruiser 79", 2021, 7, "out", "Daniel Kiptoo"),
    ("KDC 903A", "Tembo", "cruiser", "Toyota Land Cruiser 76", 2022, 6, "available", "Ali Bakari"),
    ("KCZ 118L", "Chui", "van", "Nissan Caravan 4x4 pop-top", 2018, 7, "maintenance", None),
    ("KDD 470T", "Kifaru", "cruiser", "Toyota Land Cruiser 79", 2023, 7, "available", "Moses Njoroge"),
    ("KCX 772P", "Duma", "van", "Toyota Hiace 4x4 pop-top", 2017, 7, "out", None),
    ("KDE 036B", "Nyati", "minibus", "Toyota Coaster (22 seats)", 2020, 22, "available", None),
    ("KCV 309H", "Kiboko", "minibus", "Nissan Civilian", 2016, 29, "oos", None),
]

KENYAN_FIRST = ["Wanjiru", "Kamau", "Achieng", "Otieno", "Njeri", "Mutua", "Chebet", "Kiprono", "Wambui", "Omondi",
                "Nyambura", "Kiptoo", "Akinyi", "Mwangi", "Muthoni", "Ochieng", "Jeptoo", "Kariuki", "Wairimu",
                "Barasa", "Nekesa", "Wafula", "Adhiambo", "Karanja", "Makena", "Kimani", "Auma", "Njoroge", "Zawadi",
                "Baraka", "Amani", "Imani", "Halima", "Hassan", "Fatuma", "Salim"]
KENYAN_LAST = ["Mwangi", "Otieno", "Kamau", "Wanjiku", "Ochieng", "Kiprop", "Mutiso", "Njoroge", "Omondi", "Kilonzo",
               "Chege", "Wekesa", "Korir", "Ndungu", "Odhiambo", "Muriuki", "Rotich", "Nyaga", "Kibet", "Maina"]
INTERNATIONAL = [("Emma Thompson", "British", "+44 7700 900{:03d}"), ("Lukas Schneider", "German", "+49 1512 345{:04d}"),
                 ("Chloe Martin", "French", "+33 6 12 34 5{:03d}"), ("Michael Johnson", "American", "+1 202 555 0{:03d}"),
                 ("Giulia Rossi", "Italian", "+39 347 123 4{:03d}"), ("Wanjiku Brown", "Kenyan (UK diaspora)", "+44 7911 123{:03d}"),
                 ("Ahmed Al-Farsi", "Emirati", "+971 50 123 4{:03d}"), ("Sophie van Dijk", "Dutch", "+31 6 1234 5{:03d}"),
                 ("Daniel Kim", "Korean-American", "+1 415 555 0{:03d}"), ("Olivia Wilson", "Australian", "+61 412 345 {:03d}"),
                 ("Priya Sharma", "Indian", "+91 98200 1{:04d}"), ("Kofi Mensah", "Ghanaian", "+233 24 123 4{:03d}")]

LOST_REASONS = ["Price too high", "Chose another operator", "Dates didn't work", "No response after quote",
                "Visa issues", "Postponed to next year"]
NOTES = ["Asked for a family room.", "Celebrating an anniversary.", "Wants to see the river crossing.",
         "Vegetarian in the group.", "Prefers a private vehicle.", "Asked about paying by M-PESA in instalments.",
         "Flexible within the month.", "Referred by a past client.", ""]


from ._seed_phases import SeedPhasesMixin  # noqa: E402


class Command(SeedPhasesMixin, BaseCommand):
    help = "Seed realistic demo data for Pocket of Paradise Travel Company (Phase 1: staff, catalogue, fleet, clients, enquiries)."

    def add_arguments(self, parser):
        parser.add_argument("--reset", action="store_true", help="Delete existing demo data first.")
        parser.add_argument("--seed", type=int, default=2025, help="Random seed for repeatable data.")

    def handle(self, *args, reset=False, seed=2025, **opts):
        self.rng = random.Random(seed)
        if Enquiry.objects.exists() and not reset:
            self.stdout.write(self.style.WARNING("Data already present. Run with --reset to rebuild the demo data."))
            return
        with transaction.atomic():
            if reset:
                self._reset()
            self.now = timezone.now()
            self.today = timezone.localdate()
            self._company_and_currency()
            self._staff()
            self._catalogue()
            self._fleet()
            self._clients_and_enquiries()
            self._sales()
            self._trips()
            self._finance()
            self._marketing()
            self._notifications()
            self._hr_and_system()
            self._deal_desk_and_more()
            self._itinerary_templates()
            self._attendance_history()
            self._cards_and_broadcasts()
        self._summary()

    # ------------------------------------------------------------------ helpers
    def _reset(self):
        from apps.finance.models import (Budget, Expense, FinanceEvent, Invoice, JournalEntry, KraObligation, PayrollRun,
                                         Receipt)
        from apps.marketing.models import (AutomationWorkflow, Campaign, HashtagSet, InboxMessage, MediaAsset, Post, Review,
                                           SocialAccount, TrackedLink)
        from apps.notifications.models import OutboundMessage
        from apps.sales.models import DoubleAllocation
        from apps.suppliers.models import Bill, Supplier
        from apps.frontdesk.models import Appointment, EquipmentRecord, ShiftNote, Visitor
        from apps.marketing.models import ContentShoot
        from apps.system.models import AuditLog, ImportBatch, IntegrationCredential, TodoItem
        from apps.hr.models import AttendanceRecord, LeaveEntitlement, LeaveRequest, OfficeLocation
        from apps.frontdesk.models import BusinessCard
        from apps.ict.models import Broadcast, OptOut
        from apps.schedule.models import CalendarEvent
        from apps.compliance.models import DataBreach, DataRequest, RetentionRun
        from apps.assets.models import DepreciationRun, FixedAsset
        from apps.dealdesk.models import BudgetRequest, ClientKYC, PaymentPlan, SignatureRequest
        from apps.ict.models import BotConversation, BotSettings
        from apps.reports.models import ScheduledReport
        from apps.sales.models import ItineraryTemplate
        from apps.trips.models import Departure

        for model in (Broadcast, OptOut, BusinessCard, AttendanceRecord, OfficeLocation, ItineraryTemplate, ScheduledReport, BotConversation, BotSettings, DepreciationRun, FixedAsset, BudgetRequest, SignatureRequest, PaymentPlan, ClientKYC, LeaveRequest, LeaveEntitlement, CalendarEvent, DataRequest, DataBreach, RetentionRun, ImportBatch, IntegrationCredential, AuditLog, TodoItem, Visitor, Appointment, EquipmentRecord, ShiftNote, ContentShoot, JournalEntry, Receipt, Expense, Invoice, PayrollRun, KraObligation, Budget, FinanceEvent, Bill, Supplier,
                      Review, InboxMessage, Post, MediaAsset, HashtagSet, TrackedLink, SocialAccount, AutomationWorkflow,
                      OutboundMessage, DoubleAllocation, BookingPayment, Booking, Departure, Campaign, QuotationItem, Quotation, CallLog, FollowUp, AssignmentRule, SalesTarget,
                      SalesSettings, Notification, LeadActivity, LeadAssignment, TravelPartyMember, Enquiry, Client, FuelLog, Vehicle,
                      TourPackage, ItineraryDay, Itinerary, RoomRate, Accommodation, Destination, Employee, Specialty,
                      Department, ExchangeRate):
            model.objects.all().delete()
        Enquiry.history.all().delete()
        Sequence.objects.all().delete()
        User.objects.filter(email__endswith=f"@{DOMAIN}").delete()

    def _dt(self, days_ago: float, hour=None):
        d = self.today - timedelta(days=int(days_ago))
        h = hour if hour is not None else self.rng.randint(7, 19)
        dt = timezone.make_aware(datetime.combine(d, time(h, self.rng.randint(0, 59))))
        return min(dt, self.now - timedelta(minutes=self.rng.randint(5, 50)))

    # ------------------------------------------------------------------ company, FX
    def _company_and_currency(self):
        c = CompanySetting.load()
        c.name, c.tagline = "Pocket of Paradise Travel Company", "Travel Company"
        c.kra_pin, c.tra_license_no, c.paybill = "P051234567X", "TRA/TA/2025/0142", "522533"
        c.phone, c.email, c.website = "+254 712 000 111", f"info@{DOMAIN}", f"https://{DOMAIN}"
        c.address = "Kenyatta Avenue, 4th Floor\nP.O. Box 12345-00100\nNairobi, Kenya"
        c.save()
        for code, name, sym, base, order in (("KES", "Kenya Shilling", "KES", True, 0), ("USD", "US Dollar", "$", False, 1),
                                              ("EUR", "Euro", "€", False, 2), ("GBP", "Pound Sterling", "£", False, 3)):
            Currency.objects.update_or_create(code=code, defaults={"name": name, "symbol": sym, "is_base": base,
                                                                   "sort_order": order, "is_active": True})
        base = {"USD": Decimal("129.20"), "EUR": Decimal("140.60"), "GBP": Decimal("164.30")}
        for code, val in base.items():
            cur = Currency.objects.get(code=code)
            for i in range(30, -1, -1):
                drift = Decimal(str(round(self.rng.uniform(-0.006, 0.006), 5)))
                ExchangeRate.objects.update_or_create(
                    currency=cur, date=self.today - timedelta(days=i),
                    defaults={"rate_to_base": (val * (1 + drift)).quantize(Decimal("0.0001")), "source": "seed"})

    # ------------------------------------------------------------------ people
    def _staff(self):
        self.specialties = {n: Specialty.objects.get_or_create(name=n, defaults={"slug": n.lower().replace(" / ", "-")
                                                                                    .replace(" ", "-")})[0]
                            for n in SPECIALTIES}
        self.depts = {}
        self.employees = {}
        self.users = {}
        for i, (name, local, title, dept, role_names, flags, specs, langs) in enumerate(STAFF):
            d = self.depts.get(dept) or Department.objects.get_or_create(name=dept)[0]
            self.depts[dept] = d
            user = None
            if local:
                user = User.objects.create_user(f"{local}@{DOMAIN}", DEMO_PASSWORD, full_name=name,
                                                is_staff=R.ADMIN in role_names)
                user.groups.set(Group.objects.filter(name__in=role_names))
                self.users[local] = user
            phone = f"+2547{self.rng.randint(10, 99)}{self.rng.randint(100000, 999999)}"
            emp = Employee.objects.create(
                user=user, full_name=name, title=title, department=d, phone=phone,
                email=f"{(local or name.split()[0].lower())}@{DOMAIN}", languages=langs,
                basic_salary=Decimal(self.rng.choice([38000, 42000, 45000, 65000, 85000, 120000])),
                payout_method="mpesa" if flags.get("is_driver") else "bank", bank_name="KCB Bank",
                bank_account=f"11{self.rng.randint(10000000, 99999999)}", mpesa_number=phone,
                kra_pin=f"A0{self.rng.randint(10000000, 99999999)}{chr(65 + i % 26)}",
                nssf_no=str(self.rng.randint(200000000, 299999999)), shif_no=f"SHA{self.rng.randint(100000, 999999)}",
                driving_license_no=f"DL{self.rng.randint(100000, 999999)}" if flags.get("is_driver") else "",
                driving_license_expiry=self.today + timedelta(days=self.rng.randint(90, 900)) if flags.get("is_driver") else None,
                psv_badge_no=f"PSV{self.rng.randint(10000, 99999)}" if flags.get("is_driver") else "",
                psv_badge_expiry=self.today + timedelta(days=self.rng.randint(20, 400)) if flags.get("is_driver") else None,
                guide_certification="KPSGA Silver" if flags.get("is_guide") else "",
                guide_certification_expiry=self.today + timedelta(days=self.rng.randint(60, 700)) if flags.get("is_guide") else None,
                **flags,
            )
            emp.specialties.set([self.specialties[s] for s in specs])
            self.employees[name] = emp
        # Admin is also a superuser so the Django admin works out of the box.
        admin = self.users["admin"]
        admin.is_superuser = True
        admin.save(update_fields=["is_superuser"])
        self.agents = [e for e in self.employees.values() if e.is_sales_agent]

    # ------------------------------------------------------------------ catalogue
    def _catalogue(self):
        self.destinations = {}
        for name, region, country, lat, lng, best, acts, summary, feat in DESTINATIONS:
            self.destinations[name] = Destination.objects.create(
                name=name, region=region, country=country, latitude=Decimal(str(lat)), longitude=Decimal(str(lng)),
                best_time_to_visit=best, activities=acts, summary=summary, is_featured=feat,
                description=f"{summary}\n\nOur guides know {name} well and plan each day around wildlife activity, "
                            f"road conditions and your group's pace.")
        self.accommodations = {}
        for name, dest, typ, stars in ACCOMMODATIONS:
            acc = Accommodation.objects.create(
                name=name, destination=self.destinations[dest], accommodation_type=typ, star_rating=stars,
                contact_name=self.rng.choice(["Reservations desk", "Mary Wanjiru", "Tom Ouma", "Aisha Said"]),
                contact_phone=f"+2547{self.rng.randint(10, 99)}{self.rng.randint(100000, 999999)}",
                contact_email=f"reservations@{name.split()[0].lower()}.co.ke",
                contract_notes="Net rates valid for the current contract year. 30% deposit to confirm, "
                               "balance 30 days before arrival.")
            base = Decimal(90 + stars * 45)
            yr = self.today.year
            for season, mult, start, end in (("low", Decimal("0.75"), date(yr, 4, 1), date(yr, 6, 30)),
                                              ("high", Decimal("1.0"), date(yr, 1, 3), date(yr, 3, 31)),
                                              ("peak", Decimal("1.3"), date(yr, 7, 1), date(yr, 10, 31))):
                RoomRate.objects.create(accommodation=acc, room_type="Double / Twin", season=season,
                                        rate_per_person=(base * mult).quantize(Decimal("1")), currency="USD",
                                        valid_from=start, valid_to=end,
                                        board_basis="Full board" if typ in ("lodge", "camp") else "Half board")
            self.accommodations[name] = acc
        self.packages = []
        for (title, typ, days, nights, cur, price, supp, child, status, dests, diff, plan) in PACKAGES:
            itin = Itinerary.objects.create(title=title)
            pkg = TourPackage.objects.create(
                title=title, package_type=typ, days=days, nights=nights, currency=cur,
                price_per_person=Decimal(price), single_supplement=Decimal(supp), child_rate=Decimal(child),
                status=status, difficulty=diff, itinerary=itin, min_group=1 if typ != "group" else 2,
                max_group=7 if typ != "group" else 14,
                summary=f"{days}-day {dict(TourPackage.TYPE_CHOICES)[typ].lower()} package covering {', '.join(dests)}.",
                inclusions="Transport in a 4x4 safari vehicle with pop-up roof\nProfessional English-speaking driver-guide\n"
                           "Park entry fees\nAccommodation as per itinerary\nMeals as per itinerary\nDrinking water",
                exclusions="International flights and visas\nTravel insurance\nTips and gratuities\n"
                           "Optional activities (balloon safari, village visits)\nDrinks at lodges")
            pkg.destinations.set([self.destinations[d] for d in dests])
            for n, (dt, dest, acc, meals, km, hrs, acts) in enumerate(plan, start=1):
                ItineraryDay.objects.create(
                    itinerary=itin, day_number=n, title=dt, destination=self.destinations.get(dest),
                    accommodation=self.accommodations.get(acc) if acc else None,
                    breakfast="B" in meals, lunch="L" in meals, dinner="D" in meals, activities=acts,
                    drive_km=km, drive_hours=Decimal(str(hrs)) if hrs else None,
                    description=f"{dt}. " + (f"Activities: {acts.lower()}." if acts else "Free time and transfers."))
            self.packages.append(pkg)

    # ------------------------------------------------------------------ fleet
    def _fleet(self):
        for reg, nick, typ, model, year, seats, status, driver in VEHICLES:
            ins = self.today + timedelta(days=self.rng.randint(-10, 330))
            insp = self.today + timedelta(days=self.rng.randint(5, 200))
            svc = self.today + timedelta(days=self.rng.randint(-5, 60))
            v = Vehicle.objects.create(
                registration_no=reg, nickname=nick, vehicle_type=typ, make_model=model, year=year, seats=seats,
                status=status, current_driver=self.employees.get(driver) if driver else None,
                odometer_km=self.rng.randint(38000, 240000), insurance_expiry=ins, inspection_expiry=insp,
                last_service_date=svc - timedelta(days=90), next_service_due=svc,
                next_service_km=None, notes="Fridge, charging ports, pop-up roof." if typ != "minibus" else "")
            odo = v.odometer_km - 4000
            for k in range(self.rng.randint(4, 9)):
                odo += self.rng.randint(300, 700)
                litres = Decimal(self.rng.randint(45, 90))
                FuelLog.objects.create(vehicle=v, date=self.today - timedelta(days=k * 4 + self.rng.randint(0, 3)),
                                       litres=litres, amount=(litres * Decimal("179.30")).quantize(Decimal("1")),
                                       odometer_km=odo, station=self.rng.choice(["Rubis Karen", "TotalEnergies Narok",
                                                                                 "Shell Westlands", "Ola Emali"]),
                                       filled_by=v.current_driver)

    # ------------------------------------------------------------------ clients & enquiries
    def _new_client(self, i):
        if self.rng.random() < 0.22:
            name, nat, fmt = self.rng.choice(INTERNATIONAL)
            first = name.split()[0]
            name = f"{first} {name.split()[1]}" if self.rng.random() < .5 else \
                f"{first} {self.rng.choice(['Smith', 'Müller', 'Brown', 'Davis', 'Clarke', 'Moreau', 'Bianchi'])}"
            phone = fmt.format(i % 1000 if "{:03d}" in fmt else i)
            email = f"{name.lower().replace(' ', '.').replace('ü', 'u')}{i}@example.com"
        else:
            name = f"{self.rng.choice(KENYAN_FIRST)} {self.rng.choice(KENYAN_LAST)}"
            nat = "Kenyan"
            phone = f"07{self.rng.choice([0, 1, 2, 4, 9])}{self.rng.randint(1000000, 9999999)}"
            email = f"{name.lower().replace(' ', '.')}{i}@gmail.com" if self.rng.random() < .6 else ""
        c = Client.objects.create(full_name=name, phone=phone, email=email, nationality=nat,
                                  preferred_language="English",
                                  dietary_notes=self.rng.choice(["", "", "", "Vegetarian", "No pork", "Gluten-free"]),
                                  passport_number=f"A{self.rng.randint(1000000, 9999999)}" if nat != "Kenyan" else "")
        return c

    def _clients_and_enquiries(self):
        rng = self.rng
        stage_counts = [(Stage.ENQUIRY, 86), (Stage.QUOTED, 64), (Stage.HOT, 38), (Stage.ITINERARY, 29),
                        (Stage.DEPOSIT, 14), (Stage.CONFIRMED, 9), (Stage.COMPLETED, 16), (Stage.LOST, 21)]
        marketing_sources = (["facebook"] * 72 + ["instagram"] * 58 + ["tiktok"] * 46 + ["google"] * 38
                             + ["youtube"] * 34 + ["website"] * 20 + ["referral"] * 12 + ["agent"] * 6
                             + ["tripadvisor"] * 6 + ["whatsapp"] * 40 + ["call"] * 25 + ["walk_in"] * 15 + ["dm"] * 15)
        today_sources = ["whatsapp"] * 7 + ["call"] * 5 + ["walk_in"] * 4 + ["dm"] * 2  # 18 enquiries today
        available_agents = [a for a in self.agents if a.is_available]
        clients: list[Client] = []
        purposes = ["new"] * 6 + ["custom"] * 2 + ["group", "repeat", "other"]
        unassigned_left = 23
        n = 0
        plan = []
        for stage, count in stage_counts:
            plan += [stage] * count
        rng.shuffle(plan)
        # force 18 of the "enquiry" stage to be today's desk enquiries
        todays = 0
        users = list(self.users.values())
        frontdesk = self.users["frontdesk"]
        for stage in plan:
            n += 1
            is_today = stage == Stage.ENQUIRY and todays < 18
            if is_today:
                source = today_sources[todays]
                todays += 1
                created = self.now - timedelta(minutes=10 + (18 - todays) * 17)
            else:
                source = rng.choice(marketing_sources)
                age = {Stage.ENQUIRY: rng.uniform(1, 25), Stage.QUOTED: rng.uniform(3, 40), Stage.HOT: rng.uniform(5, 50),
                       Stage.ITINERARY: rng.uniform(7, 60), Stage.DEPOSIT: rng.uniform(10, 80),
                       Stage.CONFIRMED: rng.uniform(15, 100), Stage.COMPLETED: rng.uniform(60, 180),
                       Stage.LOST: rng.uniform(10, 150)}[stage]
                created = self._dt(age)
            # 15% of enquiries come from returning clients
            if clients and rng.random() < 0.15:
                client, returning = rng.choice(clients), True
            else:
                client, returning = self._new_client(n), False
                clients.append(client)
            pkg = rng.choice(self.packages + [None, None])
            dest = pkg.destinations.first() if pkg else rng.choice(list(self.destinations.values()))
            if client.is_international and rng.random() < .5:
                cur, bmin = "USD", rng.choice([1500, 2000, 2500, 3500])
                budget = f"${bmin:,}–${bmin + 1000:,} pp"
            else:
                cur, bmin = "KES", rng.choice([30000, 45000, 60000, 80000, 120000])
                budget = f"KES {bmin // 1000}K–{(bmin + 20000) // 1000}K pp"
            open_stage = stage not in (Stage.COMPLETED, Stage.LOST)
            agent = None
            if not (open_stage and stage in (Stage.ENQUIRY, Stage.QUOTED) and unassigned_left > 0 and rng.random() < .3):
                if client.is_international and rng.random() < .6:
                    agent = self.employees["Mercy Atieno"]
                elif dest and dest.region == "coast" and rng.random() < .7:
                    agent = self.employees["Peter Odhiambo"]
                else:
                    agent = rng.choice(self.agents)
            elif stage == Stage.ENQUIRY or stage == Stage.QUOTED:
                unassigned_left -= 1
            travel_from = (created.date() + timedelta(days=rng.randint(14, 150))) if stage != Stage.COMPLETED else \
                (created.date() + timedelta(days=rng.randint(10, 40)))
            adults = rng.choice([1, 2, 2, 2, 3, 4, 6, 12])
            e = Enquiry.objects.create(
                client=client, package=pkg, destination=dest, source=source, captured_by=frontdesk if source in
                ("whatsapp", "call", "walk_in", "dm") else None,
                captured_via="front_desk" if source in ("whatsapp", "call", "walk_in", "dm") else "marketing_sync",
                purpose="group" if adults >= 10 else ("repeat" if returning else rng.choice(purposes)),
                adults=adults, children=rng.choice([0, 0, 0, 1, 2]), dates_flexible=rng.random() < .6,
                travel_date_from=travel_from, travel_date_to=travel_from + timedelta(days=(pkg.days - 1) if pkg else 3),
                budget_currency=cur, budget_min_pp=Decimal(bmin), budget_max_pp=Decimal(bmin + (1000 if cur == "USD" else 20000)),
                budget_text=budget, notes=rng.choice(NOTES), stage=stage, is_returning_client=returning,
                lost_reason=rng.choice(LOST_REASONS) if stage == Stage.LOST else "",
                quotation_status=QuotationStatus.NOT_QUOTED if stage == Stage.ENQUIRY else
                (QuotationStatus.REVISED if rng.random() < .2 else QuotationStatus.QUOTED),
                assigned_agent=agent,
                next_action=rng.choice(STAGE_NEXT.get(stage, [""])) if open_stage else "",
            )
            stage_age = (self.now - created).total_seconds() / 86400
            stage_changed = created + timedelta(days=rng.uniform(0, max(stage_age - .1, 0))) if stage != Stage.ENQUIRY \
                else created
            last_contact = None
            acts = [LeadActivity(enquiry=e, activity_type="created", timestamp=created,
                                 details=f"Captured via {e.get_source_display()}",
                                 user=frontdesk if e.captured_via == "front_desk" else None)]
            if agent:
                assigned_at = created + timedelta(minutes=rng.randint(3, 90))
                LeadAssignment.objects.create(enquiry=e, agent=agent, assigned_at=min(assigned_at, self.now),
                                              method=rng.choice(["manual", "round_robin", "rule"]),
                                              assigned_by=self.users["salesmanager"])
                acts.append(LeadActivity(enquiry=e, activity_type="assignment", timestamp=min(assigned_at, self.now),
                                         details=f"Assigned to {agent}", user=self.users["salesmanager"]))
                for _ in range(rng.randint(0, 3) if stage != Stage.ENQUIRY else rng.randint(0, 1)):
                    t = min(created + timedelta(hours=rng.uniform(1, max(stage_age * 24, 2))), self.now)
                    typ = rng.choice(["call", "whatsapp", "whatsapp", "email"])
                    acts.append(LeadActivity(enquiry=e, activity_type=typ, timestamp=t, user=agent.user,
                                             details=rng.choice(["Discussed dates and party size.", "Sent package details.",
                                                                 "Client comparing two options.", "No answer, will retry.",
                                                                 "Client asked about child rates."])))
                    last_contact = max(last_contact, t) if last_contact else t
                if stage != Stage.ENQUIRY:
                    acts.append(LeadActivity(enquiry=e, activity_type="quotation", timestamp=stage_changed, user=agent.user,
                                             details="Quotation sent."))
                    acts.append(LeadActivity(enquiry=e, activity_type="stage", timestamp=stage_changed, user=agent.user,
                                             details=f"Enquiry → {e.get_stage_display()}"
                                                     + (f" ({e.lost_reason})" if e.lost_reason else "")))
                    last_contact = max(last_contact, stage_changed) if last_contact else stage_changed
            LeadActivity.objects.bulk_create(acts)
            if adults + e.children > 1 and rng.random() < .35:
                for k in range(min(adults + e.children - 1, 3)):
                    TravelPartyMember.objects.create(
                        enquiry=e, full_name=f"{rng.choice(KENYAN_FIRST)} {client.full_name.split()[-1]}",
                        relationship=rng.choice(["spouse", "family", "friend", "child"]), is_child=k < e.children,
                        age=rng.randint(5, 15) if k < e.children else None)
            Enquiry.objects.filter(pk=e.pk).update(created_at=created, stage_changed_at=stage_changed,
                                                   last_contact_at=last_contact)
        # client "added" date = their first enquiry
        for c in Client.objects.prefetch_related("enquiries"):
            first = min((x.created_at for x in c.enquiries.all()), default=None)
            if first:
                Client.objects.filter(pk=c.pk).update(created_at=first)

    # ------------------------------------------------------------------ sales (Phase 3)
    def _sales(self):
        rng = self.rng
        SalesSettings.objects.update_or_create(pk=1, defaults={"response_sla_minutes": 30, "auto_assign_new": False})
        mercy, peter = self.employees["Mercy Atieno"], self.employees["Peter Odhiambo"]
        AssignmentRule.objects.create(name="Diaspora & international clients", priority=1, international_only=True,
                                      agent=mercy)
        for i, d in enumerate(Destination.objects.filter(region="coast")):
            AssignmentRule.objects.create(name=f"{d.name} specialist", priority=5 + i, destination=d, agent=peter)
        for c in Client.objects.prefetch_related("enquiries"):  # the agent on a client's latest lead owns them
            latest = next((e for e in sorted(c.enquiries.all(), key=lambda x: -x.pk) if e.assigned_agent_id), None)
            if latest:
                Client.objects.filter(pk=c.pk).update(owner=latest.assigned_agent, owner_assigned_at=latest.created_at)
        fx = {c: sales_services.fx_rate(c) for c in ("KES", "USD", "EUR", "GBP")}
        today = self.today
        for e in Enquiry.objects.select_related("package", "client", "assigned_agent").order_by("pk"):
            cur = e.package.currency if e.package else e.budget_currency
            cur = cur if cur in fx else "KES"
            party = e.adults + e.children
            if e.package and e.package.price_per_person:
                lines = [(f"{e.package.title} · per adult", Decimal(e.adults), e.package.price_per_person)]
                if e.children and e.package.child_rate:
                    lines.append((f"{e.package.title} · per child", Decimal(e.children), e.package.child_rate))
            else:
                per = (e.budget_min_pp or Decimal(60000)) * (Decimal("1.1") if e.budget_currency == cur else 1)
                lines = [(f"Safari package · {e.interest_label}", Decimal(party), per.quantize(Decimal("1")))]
            base_kes = sum((q * p for _, q, p in lines), Decimal("0")) * fx[cur]
            if e.stage in (Stage.ENQUIRY,) or not e.assigned_agent:
                Enquiry.objects.filter(pk=e.pk).update(deal_value=base_kes.quantize(Decimal("1")))
                continue
            quote_time = min(e.stage_changed_at, self.now) if e.stage != Stage.ENQUIRY else e.created_at
            versions = 2 if e.quotation_status == "revised" else 1
            final = None
            for v in range(1, versions + 1):
                last = v == versions
                if e.stage == Stage.LOST:
                    status = QStatus.REJECTED if last else QStatus.SUPERSEDED
                elif e.stage in (Stage.DEPOSIT, Stage.CONFIRMED, Stage.COMPLETED):
                    status = QStatus.ACCEPTED if last else QStatus.SUPERSEDED
                else:
                    status = QStatus.SENT if last else QStatus.SUPERSEDED
                sent_at = max(e.created_at + timedelta(hours=3), quote_time - timedelta(days=(versions - v) * 2))
                q = Quotation.objects.create(
                    enquiry=e, version=v, agent=e.assigned_agent, title=f"{e.interest_label} · {e.party_label}",
                    currency=cur, fx_rate=fx[cur], vat_rate=Decimal("16") if cur == "KES" else 0,
                    valid_until=(sent_at + timedelta(days=14)).date(), terms=sales_services.standard_terms(),
                    status=status, sent_at=sent_at, sent_via=rng.choice(["whatsapp", "email", "whatsapp"]),
                    decided_at=quote_time if last and status != QStatus.SENT else None,
                    discount=Decimal(rng.choice([0, 0, 0, 5000 if cur == "KES" else 100])) if v == versions else 0)
                for pos, (desc, qty, price) in enumerate(lines):
                    adj = Decimal("1") if v == versions else Decimal("1.06")  # earlier versions were a little pricier
                    QuotationItem.objects.create(quotation=q, position=pos, kind="other", description=desc,
                                                 quantity=qty, unit_price=(price * adj).quantize(Decimal("1")))
                q.recalculate()
                Quotation.objects.filter(pk=q.pk).update(created_at=sent_at - timedelta(hours=1))
                final = q
            Enquiry.objects.filter(pk=e.pk).update(deal_value=final.total_kes)
            if e.stage in (Stage.DEPOSIT, Stage.CONFIRMED, Stage.COMPLETED):
                status = {Stage.DEPOSIT: BookingStatus.DEPOSIT, Stage.CONFIRMED: BookingStatus.CONFIRMED,
                          Stage.COMPLETED: BookingStatus.COMPLETED}[e.stage]
                booked_at = min(self.now, max(e.created_at + timedelta(days=2), quote_time))
                b = Booking.objects.create(
                    enquiry=e, quotation=final, client=e.client, agent=e.assigned_agent, package=e.package,
                    title=final.title, travel_start=e.travel_date_from, travel_end=e.travel_date_to, adults=e.adults,
                    children=e.children, currency=cur, fx_rate=fx[cur], total_amount=final.total, deposit_percent=30,
                    status=status, booked_at=booked_at)
                Booking.objects.filter(pk=b.pk).update(booked_at=booked_at)
                dep = b.deposit_amount
                method = rng.choice(["mpesa", "mpesa", "bank", "card"])
                BookingPayment.objects.create(booking=b, amount=dep, method=method, received_at=booked_at + timedelta(days=1),
                                              reference=f"{rng.choice(['SKL', 'RJK', 'QHM'])}{rng.randint(10**6, 10**7 - 1)}",
                                              recorded_by=e.assigned_agent.user)
                if status != BookingStatus.DEPOSIT:
                    BookingPayment.objects.create(booking=b, amount=final.total - dep, method="bank",
                                                  received_at=booked_at + timedelta(days=rng.randint(5, 20)),
                                                  reference=f"TRF{rng.randint(10**5, 10**6 - 1)}",
                                                  recorded_by=e.assigned_agent.user)
        # calls mirror the "call" activities already on the timeline
        outcomes = ["connected", "connected", "interested", "no_answer", "voicemail", "busy", "not_interested"]
        for a in LeadActivity.objects.filter(activity_type="call", enquiry__assigned_agent__isnull=False) \
                .select_related("enquiry"):
            o = rng.choice(outcomes)
            CallLog.objects.create(enquiry=a.enquiry, agent=a.enquiry.assigned_agent, logged_by=a.user,
                                   direction=rng.choice(["out", "out", "in"]), outcome=o, called_at=a.timestamp,
                                   duration_seconds=rng.randint(40, 900) if o in CallLog.CONNECTED else 0,
                                   notes=a.details)
        # follow-ups for open leads, some overdue, some due today
        kinds = ["call", "whatsapp", "send_quote", "chase_payment", "email"]
        for e in Enquiry.objects.filter(stage__in=[Stage.QUOTED, Stage.HOT, Stage.ITINERARY, Stage.DEPOSIT],
                                        assigned_agent__isnull=False).exclude(next_action=""):
            due = self.now + timedelta(hours=rng.uniform(-60, 120))
            fu = FollowUp.objects.create(enquiry=e, agent=e.assigned_agent, kind=rng.choice(kinds), title=e.next_action,
                                         due_at=due)
            Enquiry.objects.filter(pk=e.pk).update(next_action_due=due)
            if rng.random() < .15:
                FollowUp.objects.filter(pk=fu.pk).update(done_at=self.now - timedelta(hours=rng.uniform(1, 40)))
        # itineraries for a sample of live leads (some already sent; stages are left untouched)
        from apps.sales.models import LeadItinerary

        for e in Enquiry.objects.filter(stage__in=[Stage.QUOTED, Stage.HOT, Stage.ITINERARY], assigned_agent__isnull=False,
                                        package__isnull=False).select_related("package", "assigned_agent")[:24]:
            it = sales_services.create_itinerary(e, e.assigned_agent.user)
            scale = 120 if it.currency == "KES" else 1                    # KES itineraries are priced in shillings
            for d in it.days.all():
                d.activities_pp = Decimal(rng.choice([0, 0, 45, 60, 90]) * scale)
                d.transport = Decimal(rng.choice([180, 220, 260]) * scale) if d.drive_km else Decimal("0")
                d.save()
            if e.stage == Stage.ITINERARY or rng.random() < 0.3:
                LeadItinerary.objects.filter(pk=it.pk).update(status="sent", sent_at=self.now - timedelta(days=rng.randint(0, 6)))
        # targets: the last six months, a little higher each month
        for agent in self.agents:
            for back in range(6):
                m = (today.replace(day=1) - timedelta(days=30 * back)).replace(day=1)
                SalesTarget.objects.update_or_create(
                    agent=agent, year=m.year, month=m.month,
                    defaults={"revenue_target": Decimal(rng.choice([1_200_000, 1_500_000, 1_800_000, 2_200_000])),
                              "bookings_target": rng.choice([4, 5, 6, 8]), "calls_target": 120})

    # ------------------------------------------------------------------ notifications
    def _notifications(self):
        items = [
            ("admin", "Vehicle KCZ 118L in maintenance", "Service overdue: booked at Toyota Kenya, Industrial Area.",
             "/fleet/", "warning"),
            ("admin", "Exchange rates refreshed", "USD, EUR and GBP rates updated from the mock provider.", "/operations/",
             "info"),
            ("salesmanager", "23 enquiries waiting for assignment", "Oldest has waited over an hour.",
             "/enquiries/?unassigned=yes", "danger"),
            ("frontdesk", "Guide Daniel Kiptoo out on trip", "KDB 551Q · Samburu · expected back 6:00 PM.", "/staff/", "info"),
        ]
        for local, title, body, url, level in items:
            Notification.objects.create(user=self.users[local], title=title, body=body, url=url, level=level)
        for local in ("sarah", "grace", "mercy", "peter", "james"):
            agent = self.users[local].employee
            for e in Enquiry.objects.filter(assigned_agent=agent).order_by("-created_at")[:3]:
                Notification.objects.create(user=self.users[local], title=f"New enquiry {e.reference}",
                                            body=f"{e.client.full_name} · {e.interest_label} · {e.get_source_display()}",
                                            url=e.get_absolute_url(), level="info", created_at=e.created_at)

    def _hr_and_system(self):
        from apps.hr import services as hr
        from apps.hr.models import LeaveType
        from apps.schedule import services as cal
        from apps.system.models import TodoItem
        from apps.system.services import audit

        hr.ensure_defaults()
        from apps.hr.models import OfficeLocation

        OfficeLocation.objects.get_or_create(name="Head office, Nairobi", defaults={"latitude": "-1.286389", "longitude": "36.817223", "radius_m": 150})
        coo, sm, dm = (self.users[k].employee for k in ("coo", "salesmanager", "deskmanager"))
        ceo = self.users["director"].employee
        for e in self.employees.values():
            if not e.user_id or e in (coo, ceo):
                continue
            e.manager = coo if e in (sm, dm) or e.department.name not in ("Sales", "Front Desk") else (sm if e.department.name == "Sales" else dm)
            e.save(update_fields=["manager"])
        coo.manager = ceo
        coo.save(update_fields=["manager"])
        annual, sick = LeaveType.objects.get(code="annual"), LeaveType.objects.get(code="sick")
        d = self.today + timedelta(days=(7 - self.today.weekday()) % 7 + 14)
        for local, start, n, approve in (("sarah", d, 4, True), ("grace", d + timedelta(days=7), 2, None), ("peter", d + timedelta(days=21), 5, True)):
            emp = self.users[local].employee
            req = hr.request_leave(emp, annual, start, start + timedelta(days=n - 1), reason="Family trip")
            if approve:
                hr.decide(req, emp.manager.user, True)
        for local, text in (("sarah", "Follow up the Mara honeymoon quote"), ("salesmanager", "Review pipeline with the team"), ("director", "Approve July budget")):
            TodoItem.objects.create(user=self.users[local], text=text, due=self.today + timedelta(days=2))
        start = timezone.now().replace(hour=10, minute=0, second=0, microsecond=0) + timedelta(days=1)
        ev = cal.create_event(self.users["salesmanager"], title="Weekly sales stand-up", start=start, end=start + timedelta(hours=1), department="sales", location="Boardroom")
        cal.invite(ev, self.users["salesmanager"], self.users["sarah"])
        cal.invite(ev, self.users["salesmanager"], self.users["director"])
        audit(self.users["admin"], "seed", detail="Demo data loaded")

    def _deal_desk_and_more(self):
        from apps.assets.models import FixedAsset
        from apps.dealdesk import services as dd
        from apps.ict import services as ict
        from apps.ict.models import BotConversation, BotMessage
        from apps.reports.models import ScheduledReport
        from apps.sales.models import Booking, Quotation

        sm, admin = self.users["salesmanager"], self.users["admin"]
        bookings = list(Booking.objects.exclude(status__in=["cancelled", "completed"]).order_by("pk")[:6])
        for i, b in enumerate(bookings[:4]):
            k = dd.submit_kyc(b.client, id_type="national_id" if not b.client.is_international else "passport", id_number=f"{self.rng.randint(20000000, 39999999)}")
            if i < 3:
                dd.decide_kyc(k, sm if not (b.client.owner_id and b.client.owner.user_id == sm.pk) else admin, True)
        for b in bookings[:2]:
            if b.balance > 0:
                dd.create_plan(b, sm, count=3, first_due=self.today + timedelta(days=self.rng.randint(5, 20)), interval_days=30)
        q = Quotation.objects.filter(status__in=["sent", "accepted"]).first()
        if q:
            dd.request_signature(sm, quotation=q)
        if bookings:
            dd.request_signature(sm, booking=bookings[0])
        for name, cat, cost, yrs in (("Toyota Land Cruiser KDK 214T", "vehicle", 9800000, 7), ("Canon EOS R6 + lenses", "equipment", 540000, 4), ("Dell laptops x6", "computer", 480000, 3), ("Reception furniture", "furniture", 210000, 5)):
            FixedAsset.objects.create(name=name, category=cat, cost=Decimal(cost), useful_life_months=yrs * 12, residual_value=Decimal(cost) * Decimal("0.1"),
                                      purchase_date=self.today - timedelta(days=self.rng.randint(120, 700)), location="Head office")
        dd.request_budget(self.users["marketing"] if "marketing" in self.users else sm, title="Nairobi travel expo stand", amount=Decimal("185000"), department="Marketing", reason="Stand, banners and two staff for three days")
        dd.request_budget(sm, title="Sales team CRM training", amount=Decimal("60000"), department="Sales")
        BotConversation.objects.all().delete()
        for phone, name, lines, hand in (("+254711900100", "Amina Said", ["Hi, I'd like a 4 day Mara trip in August", "We are 2 adults"], ""),
                                         ("+254722900200", "Peter Njoroge", ["Can I pay my deposit now?"], "Customer wants to pay")):
            c = BotConversation.objects.create(phone=phone, display_name=name, status="human" if hand else "bot", handoff_reason=hand)
            for j, t in enumerate(lines):
                BotMessage.objects.create(conversation=c, sender="client", body=t)
                BotMessage.objects.create(conversation=c, sender="bot", body="Thank you! One of our consultants will confirm the details shortly." if hand else "Lovely choice! Which month are you travelling?")
        ScheduledReport.objects.get_or_create(user=self.users["director"], report_key="pipeline_by_stage", defaults={"frequency": "weekly"})

    def _cards_and_broadcasts(self):
        from apps.frontdesk.models import BusinessCard
        from apps.ict import bulk
        from apps.ict.models import Broadcast

        desk = self.users.get("reception") or self.users["admin"]
        cards = [("Grace Wambui", "grace@maraserena.co.ke", "0722 114 220", "supplier", "Mara Serena Safari Lodge", "Reservations Manager"), ("Hassan Mohamed", "hassan@diani-reef.com", "0733 220 118", "supplier", "Diani Reef Beach Resort", "Sales Executive"),
                 ("Priya Shah", "priya@safaritrails.co.uk", "+44 7700 900123", "tour_operator", "Safari Trails UK", "Product Manager"), ("Lars Becker", "lars@afrikareisen.de", "+49 1512 3456789", "tour_operator", "Afrika Reisen", "Director"),
                 ("Mercy Chebet", "mercy@kws.go.ke", "0711 502 200", "government", "Kenya Wildlife Service", "Park Liaison"), ("Tom Kariuki", "tom@safaricom.co.ke", "0700 100 200", "corporate", "Safaricom Events", "Events Lead"),
                 ("Zawadi Achieng", "zawadi@travelblog.ke", "0745 678 900", "media", "Zawadi Travels", "Travel Blogger"), ("Peter Mwangi", "peter@nairobiairways.com", "0720 998 877", "partner", "Nairobi Transfers", "Owner"),
                 ("Amelia Brown", "amelia.brown@gmail.com", "+1 415 555 0134", "client", "", ""), ("Joseph Otieno", "joseph@tsavolodge.co.ke", "0733 401 009", "supplier", "Tsavo Lodge", "Manager")]
        for name, email, phone, cat, company, title in cards:
            BusinessCard.objects.create(name=name, email=email, phone=phone, category=cat, company=company, job_title=title, notes="Met at the desk", added_by=desk)
        b = Broadcast.objects.create(channel="sms", name="July Mara offer", body="Hi {first_name}, Pocket of Paradise: 3-day Maasai Mara from KES 45,000 this July. Reply or call 0700 000 000.",
                                     audience={"clients": {"enabled": True, "sources": ["instagram", "facebook"]}}, created_by=self.users["ict"])
        if bulk.start(b):
            bulk.process(b, limit=500)

    def _attendance_history(self):
        """Three weeks of plausible clock-ins so the attendance charts have something to show."""
        from apps.hr.models import AttendanceRecord, OfficeLocation

        office = OfficeLocation.objects.first()
        if not office:
            return
        for user in self.users.values():
            for back in range(1, 22):
                day = self.today - timedelta(days=back)
                if day.weekday() >= 5 or self.rng.random() < 0.08:
                    continue
                late = self.rng.random() < 0.15
                inn = timezone.make_aware(datetime.combine(day, time(9, self.rng.randint(5, 40)) if late else time(7, self.rng.randint(35, 59))))
                out = timezone.make_aware(datetime.combine(day, time(self.rng.randint(16, 18), self.rng.randint(0, 59))))
                away = self.rng.random() < 0.12
                dist = self.rng.randint(1500, 9000) if away else self.rng.randint(5, 90)
                AttendanceRecord.objects.create(user=user, date=day, clock_in_at=inn, clock_out_at=out, in_lat=office.latitude, in_lng=office.longitude, in_office=office,
                                                in_distance_m=dist, in_at_office=not away, late=late, out_distance_m=dist, out_at_office=not away)

    def _itinerary_templates(self):
        """A small team library: package itineraries become templates, so agents can borrow single days."""
        from apps.sales.models import ItineraryTemplate, TemplateDay

        owner = self.users["salesmanager"]
        for pkg in TourPackage.objects.filter(itinerary__isnull=False)[:4]:
            t = ItineraryTemplate.objects.create(title=pkg.title, description=pkg.summary[:300], created_by=owner)
            for d in pkg.itinerary.days.all():
                TemplateDay.objects.create(template=t, day_number=d.day_number, title=d.title, description=d.description, destination=d.destination,
                                           accommodation=d.accommodation, breakfast=d.breakfast, lunch=d.lunch, dinner=d.dinner, activities=d.activities,
                                           drive_km=d.drive_km, drive_hours=d.drive_hours)

    def _summary(self):
        self.stdout.write(self.style.SUCCESS(
            f"Seeded {Employee.objects.count()} staff, {Destination.objects.count()} destinations, "
            f"{TourPackage.objects.count()} packages, {Accommodation.objects.count()} accommodations, "
            f"{Vehicle.objects.count()} vehicles, {Client.objects.count()} clients, {Enquiry.objects.count()} enquiries."))
        self.stdout.write("\nDemo logins (password for all: %s)" % DEMO_PASSWORD)
        for name, local, title, _d, role_names, *_ in STAFF:
            if local:
                self.stdout.write(f"  {local + "@" + DOMAIN:<32} {', '.join(role_names):<24} {name}")
