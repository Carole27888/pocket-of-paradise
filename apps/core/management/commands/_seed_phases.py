"""Demo data for Trips, Finance, Suppliers and Marketing (called by seed_demo)."""
import random
from datetime import date, datetime, time, timedelta
from decimal import Decimal as D

from django.utils import timezone

from apps.catalog.models import Accommodation, TourPackage
from apps.finance import services as fin
from apps.finance.models import Budget, FinanceEvent, KraObligation, PayrollRun
from apps.fleet.models import FuelLog, Vehicle
from apps.integrations.base import get_service
from apps.leads.models import Enquiry
from apps.marketing import services as mkt
from apps.marketing.models import (AdSpendDaily, Campaign, Channel, HashtagSet, InboxMessage, LinkClick, MediaAsset,
                                   Post, Review, SocialAccount, TrackedLink)
from apps.people.models import Employee
from apps.sales.models import Booking, BookingPayment, BookingStatus
from apps.suppliers.models import Bill, Supplier
from apps.trips import services as trips
from apps.trips.models import Departure, Incident, Stage


def _aware(d: date, hour=9):
    return timezone.make_aware(datetime.combine(d, time(hour, 0)))


class SeedPhasesMixin:
    # ------------------------------------------------------------------ trips
    def _trips(self):
        rng, today = self.rng, self.today
        guides = list(Employee.objects.filter(is_guide=True))
        drivers = list(Employee.objects.filter(is_driver=True))
        vehicles = list(Vehicle.objects.exclude(status="oos"))
        for b in Booking.objects.exclude(status=BookingStatus.CANCELLED).select_related("package"):
            if not b.package or not b.travel_start:
                continue
            pax = b.adults + b.children
            dep = Departure.objects.filter(package=b.package, start_date=b.travel_start).first()
            if not dep:
                dep = Departure.objects.create(package=b.package, start_date=b.travel_start,
                                               end_date=b.travel_start + timedelta(days=max(b.package.days - 1, 0)),
                                               capacity=max(7, pax))
            if dep.seats_sold + pax > dep.capacity:
                Departure.objects.filter(pk=dep.pk).update(capacity=dep.seats_sold + pax)
            Booking.objects.filter(pk=b.pk).update(departure=dep, travel_end=dep.end_date)
        pkgs = list(TourPackage.objects.exclude(status="retired"))
        for i in range(14):                                         # departures with no bookings yet
            pkg = rng.choice(pkgs)
            start = today + timedelta(days=rng.randint(-6, 45))
            Departure.objects.get_or_create(package=pkg, start_date=start, defaults={
                "end_date": start + timedelta(days=max(pkg.days - 1, 0)), "capacity": rng.choice([7, 7, 12, 14])})
        used = []
        for dep in Departure.objects.order_by("start_date"):
            end = dep.end_date
            full = dict(accommodation_confirmed=True, permits_issued=True, park_fees_paid=True, meals_arranged=True, insurance_ok=True)
            vehicle = next((v for v in rng.sample(vehicles, len(vehicles)) if not any(
                u[0] == v.pk and u[1] <= dep.end_date and u[2] >= dep.start_date for u in used)), None)
            guide = next((g for g in rng.sample(guides, len(guides)) if not any(
                u[3] == g.pk and u[1] <= dep.end_date and u[2] >= dep.start_date for u in used)), None)
            if end < today - timedelta(days=0):
                stage = Stage.COMPLETED
            elif dep.start_date <= today:
                stage = Stage.DEPARTED
            elif (dep.start_date - today).days <= 4 and rng.random() < 0.65:
                stage = Stage.READY
            else:
                stage = Stage.PLANNED
            fields = {}
            if stage != Stage.PLANNED or rng.random() < 0.55:
                if vehicle and guide:
                    fields.update(vehicle=vehicle, guide=guide, driver=rng.choice([d for d in drivers if d.pk != guide.pk] or drivers))
                    used.append((vehicle.pk, dep.start_date, dep.end_date, guide.pk))
            if stage != Stage.PLANNED:
                fields.update(full)
            elif rng.random() < 0.5:
                fields.update(accommodation_confirmed=True, meals_arranged=rng.random() < 0.5, insurance_ok=rng.random() < 0.6)
            if stage != Stage.PLANNED and not fields.get("vehicle"):
                stage = Stage.PLANNED
                for k in full:
                    fields.pop(k, None)
            stamps = {"created_at": _aware(dep.start_date - timedelta(days=rng.randint(14, 45)), 9),
                      "stage": stage, "stage_changed_at": _aware(min(today, dep.start_date) - timedelta(days=rng.randint(0, 3)))}
            if stage != Stage.PLANNED:
                stamps["resources_confirmed_at"] = _aware(dep.start_date - timedelta(days=rng.randint(2, 6)))
            if stage in (Stage.DEPARTED, Stage.COMPLETED):
                stamps["departed_at"] = _aware(dep.start_date, 7)
            if stage == Stage.COMPLETED:
                stamps["completed_at"] = _aware(end, 18)
            Departure.objects.filter(pk=dep.pk).update(**fields, **stamps)
            dep.refresh_from_db()
            if stage != Stage.PLANNED or (dep.start_date - today).days <= 3:
                trips.sync_manifest(dep)
            if stage in (Stage.DEPARTED, Stage.COMPLETED):
                trips.check_in_all(dep)
            trips.log_event(dep, "planned", "Departure planned")
            if stage != Stage.PLANNED:
                trips.log_event(dep, "ready", "All resources confirmed")
            if stage in (Stage.DEPARTED, Stage.COMPLETED):
                trips.log_event(dep, "departed", f"Departed with {dep.manifest.count()} guests")
            if stage == Stage.COMPLETED and rng.random() < 0.3:
                inc = Incident.objects.create(departure=dep, guide=dep.guide, kind=rng.choice(["vehicle", "weather", "guest", "medical"]),
                                              severity=rng.choice(["low", "low", "medium"]), reported_at=_aware(dep.start_date, 14),
                                              description=rng.choice(["Flat tyre on the Mara road, replaced in 40 minutes.",
                                                                      "Heavy rain delayed the evening game drive.",
                                                                      "A guest felt unwell; treated at the lodge clinic.",
                                                                      "Guest complaint about late pickup; apology and refund note."]),
                                              action_taken="Handled on site.", resolved_at=_aware(dep.start_date, 16))
        # Operations dispatch board shows the departures on the road as out
        for dep in Departure.objects.filter(stage=Stage.DEPARTED):
            Vehicle.objects.filter(pk=dep.vehicle_id).update(status=Vehicle.OUT)

        self._frontdesk_extras()

    def _frontdesk_extras(self):
        from apps.frontdesk.models import Appointment, EquipmentRecord, ShiftNote, Visitor

        rng, today = self.rng, self.today
        staff = list(Employee.objects.filter(is_active=True, user__isnull=False))
        for item, n in (("radio", 6), ("cooler", 5), ("binoculars", 6), ("firstaid", 4), ("tent", 3)):
            for i in range(1, n + 1):
                eq = EquipmentRecord.objects.create(item=item, code=f"{item[:3].upper()}-{i:02d}")
                if rng.random() < 0.35:
                    when = timezone.now() - timedelta(days=rng.randint(0, 4))
                    EquipmentRecord.objects.filter(pk=eq.pk).update(assigned_to=rng.choice(staff), taken_at=when,
                                                                    due_back=when + timedelta(days=rng.choice([1, 2, 3])))
        for name, reason in (("Julius Mutiso", "Supplier: tyres quote"), ("Amina Hassan", "Interview"), ("Peter Kamau", "Delivering brochures")):
            Visitor.objects.create(name=name, reason=reason, host=rng.choice(staff), badge_no=str(rng.randint(10, 99)),
                                   time_in=_aware(today, rng.randint(8, 11)), time_out=_aware(today, 13) if rng.random() < 0.5 else None)
        for title, hour in (("Lodge rep: Mara Serena rates", 10), ("Corporate team-building briefing", 14), ("School trip parents' meeting", 16)):
            Appointment.objects.create(title=title, people="External", host=rng.choice(staff), start=_aware(today, hour))
        for title, who, days, hour in (("Tour operator visit: Kenya Wildlife Services", "KWS", 1, 11), ("Driver licence renewal appointment", "Internal", 2, 9),
                                       ("Honeymoon couple: itinerary walkthrough", "Client", 3, 15), ("Travel agent partner meeting", "External", 6, 10)):
            Appointment.objects.create(title=title, people=who, host=rng.choice(staff), start=_aware(today + timedelta(days=days), hour))
        ShiftNote.objects.create(note="Vehicle KDB 551Q due back 6pm. Two walk-ins asked about Diani, handed to Peter.", author_name="Lucy Achieng")

    # ------------------------------------------------------------------ suppliers + finance
    def _finance(self):
        rng, today = self.rng, self.today
        fin.ensure_chart()
        fin.ensure_tax_settings()
        # opening capital so cash isn't negative in the demo books
        fin.post(today - timedelta(days=210), "Opening capital & bank loan",
                 [("bank", D("9000000"), 0), ("mpesa", D("400000"), 0), ("fixed_assets", D("14500000"), 0),
                  ("share_capital", 0, D("18900000")), ("bank_loan", 0, D("5000000"))], ("opening", 1, "capital"))
        # salaries so payroll has people to pay
        for e in Employee.objects.filter(is_active=True, basic_salary=0):
            base = 85000 if "Manager" in e.title or "Director" in e.title else 65000 if "Senior" in e.title else \
                32000 if (e.is_guide or e.is_driver) else 45000
            Employee.objects.filter(pk=e.pk).update(basic_salary=D(base) + D(rng.choice([0, 0, 5000, 8000])),
                                                    nssf_no=f"NSSF{rng.randint(10**6, 10**7)}", kra_pin=f"A00{rng.randint(10**7, 10**8)}Z")
        # invoices + receipts from the bookings made in the sales seed
        for b in Booking.objects.exclude(status=BookingStatus.CANCELLED).select_related("client", "quotation").order_by("booked_at"):
            issued = timezone.localtime(b.booked_at).date()
            due = None
            if b.status == BookingStatus.PENDING:
                due = today - timedelta(days=rng.choice([-5, 3, 12, 35, 70, 100]))
            elif b.status == BookingStatus.DEPOSIT:
                due = today - timedelta(days=rng.choice([-20, -8, 6, 20, 40, 65]))
            inv = fin.invoice_for_booking(b, None, issue_date=issued, due_date=due)
            for p in b.payments.order_by("received_at"):
                if p.amount <= inv.balance:
                    fin.record_receipt(inv, amount=p.amount, method=p.method if p.method != "other" else "bank", reference=p.reference,
                                       received_at=p.received_at, user=p.recorded_by)
            if b.status == BookingStatus.COMPLETED:
                end = b.travel_end or b.travel_start or issued
                fin.recognise_booking_revenue(b, None, on_date=min(end, today))
        # one unmatched M-PESA payment waiting for reconciliation
        fin.record_receipt(None, amount=D("35000"), method="mpesa", reference="SKX9H2L7QD", account_ref="INV-0099",
                           received_at=_aware(today - timedelta(days=1), 15), client=None)
        # suppliers & bills per departure
        suppliers = {}
        for name, kind, wht in [("Mara Serena Safari Lodge", "lodge", False), ("Basecamp Mara Tented Camp", "lodge", False),
                                ("Leopard Beach Resort", "hotel", False), ("Voi Safari Lodge", "lodge", False),
                                ("Amboseli Sopa Lodge", "lodge", False), ("Kenya Wildlife Service (park fees)", "park", False),
                                ("Rift Valley Transporters", "transport", True), ("Savannah Caterers", "caterer", True),
                                ("Mara Balloon Safaris", "activity", False), ("Kilimanjaro Air Charters", "airline", False)]:
            suppliers[name] = Supplier.objects.create(
                name=name, kind=kind, withholding_applies=wht, payment_terms_days=rng.choice([14, 30, 30, 45]), contact_name=rng.choice(["Reservations", "Accounts", "Sales desk"]),
                phone=f"07{rng.randint(10000000, 99999999)}", email=f"accounts@{name.split()[0].lower()}.co.ke", location="Kenya",
                bank_details=f"Paybill {rng.randint(100000, 999999)}")
        sup = list(suppliers.values())
        lodges = [s for s in sup if s.kind in ("lodge", "hotel")]
        for dep in Departure.objects.exclude(stage=Stage.CANCELLED).select_related("package"):
            pax = max(dep.seats_sold, 2)
            lines = [(rng.choice(lodges), "accommodation", D(pax * rng.choice([14000, 18000, 24000])), False),
                     (suppliers["Kenya Wildlife Service (park fees)"], "park_fees", D(pax * rng.choice([6500, 9000, 12000])), True),
                     (suppliers["Rift Valley Transporters"], "transport", D(rng.choice([28000, 45000, 60000])), False)]
            if rng.random() < 0.5:
                lines.append((suppliers["Savannah Caterers"], "catering", D(pax * 2800), False))
            for s, cat, amt, prepay in lines:
                bill_date = dep.start_date - timedelta(days=rng.randint(10, 25))
                vat = (amt * D("16") / D("116")).quantize(D("0.01")) if cat in ("transport", "catering") else D("0")
                b = Bill.objects.create(supplier=s, departure=dep, category=cat, amount=amt, vat_amount=vat, prepayment=prepay,
                                        supplier_ref=f"{s.name[:3].upper()}-{rng.randint(1000, 9999)}", bill_date=bill_date,
                                        due_date=bill_date + timedelta(days=s.payment_terms_days), description=f"{dep.package.title}")
                if bill_date > today:
                    continue
                roll = rng.random()
                if roll < 0.12:
                    continue                                    # awaiting approval
                fin.approve_bill(b)
                if dep.stage == Stage.COMPLETED or roll < 0.55:
                    fin.pay_bill(b, method="bank", reference=f"RTGS{rng.randint(10**5, 10**6)}",
                                 paid_at=_aware(min(today, b.due_date), 11))
        for dep in Departure.objects.filter(stage=Stage.COMPLETED):
            fin.release_prepayments(dep)
        # expenses: fuel from logs, rent, field expenses
        for back in range(6):
            d = (today.replace(day=1) - timedelta(days=30 * back)).replace(day=5)
            if d <= today:
                fin.record_expense(None, date=d, category="office", payee="Westlands Plaza Ltd", description=f"Office rent {d:%b %Y}",
                                   amount=D("185000"), method="bank", auto_approve=True, recurring_monthly=True)
                fin.record_expense(None, date=d + timedelta(days=4), category="office", payee="Kenya Power & Safaricom", description="Utilities & internet",
                                   amount=D(rng.randint(32000, 41000)), method="mpesa", auto_approve=True)
        guides = Employee.objects.filter(is_guide=True, user__isnull=False)
        for dep in Departure.objects.filter(stage__in=[Stage.DEPARTED, Stage.COMPLETED]).exclude(guide__isnull=True)[:10]:
            fin.record_expense(dep.guide.user, date=dep.start_date, category=rng.choice(["tips", "fuel", "park_fees", "other"]), payee="Field expense",
                               description=f"{dep.reference} incidentals", amount=D(rng.choice([1500, 3200, 4800])), method="cash", departure=dep)
        from apps.fleet.models import MaintenanceLog

        for v in rng.sample(vehicles_all := list(Vehicle.objects.all()), 5):
            MaintenanceLog.objects.create(vehicle=v, date=today - timedelta(days=rng.randint(8, 80)), kind=rng.choice(["service", "tyres", "repair"]),
                                          description=rng.choice(["Full service and oil change", "Replaced 4 tyres", "Brake pads and alignment", "Suspension repair"]),
                                          garage=rng.choice(["Toyota Kenya, Industrial Area", "DT Dobie", "Simba Colt Garage"]), cost=D(rng.choice([24500, 38000, 112000, 64000])),
                                          odometer_km=v.odometer_km)
        # payroll: last 3 closed months paid, current month in preparation
        for back in (3, 2, 1):
            first = (today.replace(day=1) - timedelta(days=30 * back)).replace(day=1)
            run = fin.prepare_payroll(first)
            for line in run.lines.filter(employee__is_guide=True)[:6]:
                fin.recalc_line(line, allowances=D(rng.choice([6000, 9000, 12000])))
            fin.advance_payroll(run)
            fin.advance_payroll(run)
            fin.pay_payroll(run)
        fin.prepare_payroll(today.replace(day=1))
        for back in range(4, -1, -1):
            first = (today.replace(day=1) - timedelta(days=30 * back)).replace(day=1)
            fin.refresh_obligations(first)
        for ob in KraObligation.objects.filter(period__lt=today.replace(day=1) - timedelta(days=45)):
            KraObligation.objects.filter(pk=ob.pk).update(status="filed", payment_ref=f"KRA{rng.randint(10**7, 10**8)}", paid_at=_aware(ob.due_date))
        # budgets
        for back in range(0, 3):
            first = (today.replace(day=1) - timedelta(days=30 * back)).replace(day=1)
            for cat, amt in [("revenue", 6500000), ("fuel", 260000), ("office", 240000), ("marketing", 180000), ("guide_wages", 320000),
                             ("accommodation", 1200000), ("park_fees", 900000), ("catering", 220000), ("transport", 420000)]:
                Budget.objects.update_or_create(period_type="month", year=first.year, index=first.month, category=cat,
                                                defaults={"amount": D(amt), "approved": True})
        for title, d, cat in [("Month-end close", today.replace(day=28), "close"), ("Budget review", today + timedelta(days=9), "review"),
                              ("Audit prep meeting", today + timedelta(days=15), "other")]:
            FinanceEvent.objects.create(title=title, date=d, category=cat)

    # ------------------------------------------------------------------ marketing
    def _marketing(self):
        rng, today = self.rng, self.today
        mkt.ensure_channels()
        mkt.ensure_workflows()
        chans = {c.key: c for c in Channel.objects.all()}
        for ch in Channel.objects.filter(is_social=True):
            exp = timezone.now() + timedelta(days=(4 if ch.key == "tiktok" else 52))
            SocialAccount.objects.update_or_create(channel=ch, defaults={"handle": "@pocketofparadise", "connected": ch.key != "tripadvisor",
                                                                           "access_token": "seed-token", "expires_at": exp})
        specs = [("Mara Migration 2026", "bookings", ["facebook", "instagram", "google"], 400000, "active"),
                 ("TikTok Beach Escapes", "enquiries", ["tiktok", "instagram"], 150000, "active"),
                 ("YouTube Safari Stories", "awareness", ["youtube"], 120000, "active"),
                 ("Diaspora December", "promo", ["facebook", "google"], 250000, "active"),
                 ("Easter Family Safari", "promo", ["facebook", "instagram"], 180000, "completed"),
                 ("Mt Kenya Trekking Launch", "enquiries", ["google", "facebook"], 90000, "paused")]
        camps = []
        for name, obj, keys, budget, status in specs:
            c = Campaign.objects.create(name=name, objective=obj, budget=D(budget), status=status,
                                        start_date=today - timedelta(days=rng.randint(40, 120)), audience_notes="Nairobi & diaspora, 28-55",
                                        package=rng.choice(list(TourPackage.objects.exclude(status="retired"))))
            c.channels.set([chans[k] for k in keys])
            camps.append(c)
        for back in range(45, 0, -1):                       # history of channel stats and ad spend
            day = today - timedelta(days=back)
            mkt.sync_channel_stats(day)
            mkt.sync_ad_spend(day)
        # attribute existing enquiries to campaigns by their source channel
        by_src = {k: [c for c in camps if chans[k] in c.channels.all()] for k in ("facebook", "instagram", "tiktok", "google", "youtube")}
        for e in Enquiry.objects.filter(source__in=by_src.keys()):
            if by_src[e.source] and rng.random() < 0.7:
                Enquiry.objects.filter(pk=e.pk).update(campaign=rng.choice(by_src[e.source]))
        mkt.post_ad_spend_to_finance()
        # links with clicks
        for slug, name, camp, key in [("mara-fb", "Mara Facebook ad", camps[0], "facebook"), ("beach-tt", "TikTok beach reel", camps[1], "tiktok"),
                                      ("yt-stories", "YouTube description", camps[2], "youtube"), ("dec-diaspora", "Diaspora December", camps[3], "facebook")]:
            link = TrackedLink.objects.create(slug=slug, name=name, destination="https://pocketofparadise.co.ke/safaris", campaign=camp, channel=chans[key])
            for _ in range(rng.randint(40, 160)):
                LinkClick.objects.create(link=link, at=_aware(today - timedelta(days=rng.randint(0, 40)), rng.randint(7, 22)))
            for e in Enquiry.objects.filter(source=key, campaign=camp)[:rng.randint(2, 7)]:
                Enquiry.objects.filter(pk=e.pk).update(tracked_link=link)
        # content
        pk = list(TourPackage.objects.exclude(status="retired"))
        for i, (title, fmt, status) in enumerate([
                ("Migration crossing highlights", "reel", "published"), ("Diani sunrise", "photo", "published"), ("Meet our guides: Daniel", "video", "published"),
                ("Amboseli elephants & Kili", "carousel", "published"), ("Honeymoon in the Mara", "reel", "published"), ("Packing list for safari", "article", "scheduled"),
                ("December diaspora offer", "photo", "scheduled"), ("Mt Kenya trek prep", "video", "needs_approval"), ("Behind the scenes: vehicle prep", "story", "draft")]):
            p = Post.objects.create(title=title, format=fmt, status=status, package=rng.choice(pk), caption="Book your safari with Pocket of Paradise Travel Company. Where everyday is an exploration.",
                                    scheduled_at=_aware(today + timedelta(days=rng.randint(-20, 14)), rng.choice([9, 12, 18])))
            p.channels.set(rng.sample(list(Channel.objects.filter(is_social=True)), rng.randint(1, 3)))
            if status == "published":
                Post.objects.filter(pk=p.pk).update(published_at=p.scheduled_at, reach=rng.randint(4000, 52000), engagement=rng.randint(200, 4200),
                                                    leads=rng.randint(2, 28), bookings=rng.randint(0, 4))
        for t, k in [("Mara lioness at dawn", "photo"), ("Balloon safari clip", "video"), ("Diani drone footage", "video"), ("Amboseli itinerary", "pdf"), ("Giraffe Centre", "photo")]:
            MediaAsset.objects.create(title=t, kind=k, package=rng.choice(pk), tags="safari, kenya")
        HashtagSet.objects.create(name="Safari general", tags="#kenyasafari #masaimara #safarilife #visitkenya #pocketofparadise")
        HashtagSet.objects.create(name="Beach", tags="#dianibeach #kenyacoast #beachholiday #zanzibarvibes")
        from apps.marketing.models import ContentShoot

        crew = list(Employee.objects.filter(user__email__regex=r"^(kelvin|tony|diana|production)"))[:3] or list(Employee.objects.filter(is_active=True)[:2])
        deps = list(Departure.objects.exclude(stage="cancelled").select_related("package").order_by("-start_date")[:14])
        for i, dep in enumerate(deps):
            stage = ["posted", "shot", "sourced", "sourced"][i % 4] if dep.stage == "completed" else "sourced"
            s = ContentShoot.objects.create(title=f"{dep.package.title}: highlights reel", departure=dep, package=dep.package, stage=stage,
                                            assigned_to=rng.choice(crew), shoot_date=dep.start_date,
                                            shot_at=_aware(dep.end_date) if stage != "sourced" else None, posted_at=_aware(dep.end_date + timedelta(days=3)) if stage == "posted" else None,
                                            stage_changed_at=_aware(min(today, dep.end_date) + timedelta(days=1)))
            if stage == "posted":
                ContentShoot.objects.filter(pk=s.pk).update(post=Post.objects.filter(status="published").first())
        # reviews & inbox history
        for source, svc in (("google", "google_reviews"), ("tripadvisor", "tripadvisor")):
            for r in get_service(svc).latest(source, today - timedelta(days=150)):
                rv = Review.objects.create(source=source, external_id=r["external_id"], guest_name=r["guest_name"], rating=r["rating"],
                                           text=r["text"], review_date=r["review_date"], package=rng.choice(pk))
                if rng.random() < 0.7:
                    Review.objects.filter(pk=rv.pk).update(replied=True, reply_text="Thank you so much for travelling with us, we hope to see you again!",
                                                           replied_at=_aware(r["review_date"] + timedelta(days=rng.randint(0, 3)), 10))
        for ch in Channel.objects.filter(key__in=["facebook", "instagram", "tiktok"]):
            for m in get_service("inbox").fetch(ch.key, today - timedelta(days=10)):
                InboxMessage.objects.get_or_create(channel=ch, external_id=m["external_id"], defaults={
                    "sender": m["sender"], "message": m["message"], "kind": m["kind"], "received_at": _aware(m["received_at"], 10),
                    "replied": rng.random() < 0.5})
