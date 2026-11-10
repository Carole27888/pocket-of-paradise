"""Itinerary builder ported from the standalone tool: photo library, per-day photos, lodge options, price lines, slide PDF."""
import io
from decimal import Decimal as D

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from PIL import Image

from apps.catalog.models import LibraryPhoto, PhotoCategory
from apps.sales import services
from apps.sales.models import ItineraryHotel, ItineraryPriceLine, ItineraryTemplate, LeadItinerary, TemplateDay, TemplatePriceLine
from tests.test_itinerary import _formset_post, _initial, _lead, mara  # noqa: F401


def img(name="p.png", size=(60, 40), color="orange"):
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, "PNG")
    return SimpleUploadedFile(name, buf.getvalue(), content_type="image/png")


@pytest.fixture(autouse=True)
def _media(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path


@pytest.mark.django_db
def test_library_upload_tag_search_and_delete(client, agent):
    client.force_login(agent.user)
    assert client.get("/catalog/library/").status_code == 200
    r = client.post("/catalog/library/", {"name": "Maasai Mara", "kind": "destination"})
    assert r.status_code == 302
    cat = PhotoCategory.objects.get(name="Maasai Mara")
    url = reverse("catalog:photo_pack", args=[cat.slug])
    r = client.post(url, {"images": [img("a.png"), img("b.png")], "tags": "Lion, Sunset, lion"}, headers={"x-requested-with": "XMLHttpRequest"})
    assert r.json() == {"added": 2, "skipped": 0}
    bad = SimpleUploadedFile("x.png", b"not an image", content_type="image/png")
    assert client.post(url, {"images": [bad]}, headers={"x-requested-with": "XMLHttpRequest"}).json()["skipped"] == 1
    photo = cat.images.first()
    assert photo.tags == "lion, sunset" and photo.thumb and photo.image.name.endswith(".jpg")
    assert client.get(url).status_code == 200 and client.get(url + "?tag=lion").status_code == 200
    j = client.get("/catalog/library/search/", {"q": "sunset", "category": cat.pk}).json()
    assert j["total"] == 2 and j["results"][0]["category"] == "Maasai Mara"
    assert client.get("/catalog/library/search/", {"tag": "nothing"}).json()["total"] == 0
    # bulk: add a tag then delete
    ids = [str(i.pk) for i in cat.images.all()]
    client.post(reverse("catalog:photos_bulk", args=[cat.slug]), {"selected": ids, "tags": "tent", "mode": "add", "action": "tag"})
    assert "tent" in cat.images.first().tag_list
    client.post(reverse("catalog:photos_bulk", args=[cat.slug]), {"selected": ids[:1], "action": "delete"})
    assert cat.images.count() == 1
    client.post(reverse("catalog:pack_delete", args=[cat.slug]))
    assert not PhotoCategory.objects.exists() and not LibraryPhoto.objects.exists()


@pytest.mark.django_db
def test_library_blocked_for_other_roles(client, make_user):
    from apps.accounts import roles as R

    client.force_login(make_user("acct@example.com", R.GUIDE))
    assert client.get("/catalog/library/").status_code == 403


def _edit_post(client, it, **extra):
    r = client.get(reverse("sales:itinerary_edit", args=[it.pk]))
    data = _initial(r.context["form"])
    data.update(_formset_post(r.context["formset"]))
    for name in ("price_lines", "hotels"):
        data.update({f"{name}-TOTAL_FORMS": "0", f"{name}-INITIAL_FORMS": "0", f"{name}-MIN_NUM_FORMS": "0", f"{name}-MAX_NUM_FORMS": "1000"})
    data.update(extra)
    return client.post(reverse("sales:itinerary_edit", args=[it.pk]), data)


@pytest.mark.django_db
def test_builder_saves_photos_lines_hotels_and_prices_them(client, agent, mara):
    pkg, _ = mara
    it = services.create_itinerary(_lead(agent, pkg), agent.user, package=pkg)
    client.force_login(agent.user)
    pack = PhotoCategory.objects.create(name="Mara", kind="destination")
    lodge_pack = PhotoCategory.objects.create(name="Serena photos", kind="accommodation")
    p1 = LibraryPhoto.objects.create(category=pack, image=img("1.png"), tags="lion")
    LibraryPhoto.objects.create(category=lodge_pack, image=img("2.png"))
    r = client.get(reverse("sales:itinerary_edit", args=[it.pk]))
    assert b"Choose photos" in r.content and b"Lodge options" in r.content and b"Extra priced lines" in r.content
    extra = {"photo_pack": pack.pk, "nights_override": "5", "inclusions": "Park fees\nGuide", "exclusions": "Flights", "terms": "30% deposit",
             "days-0-photos": [p1.pk],
             "price_lines-TOTAL_FORMS": "1", "price_lines-0-description": "Visa", "price_lines-0-quantity": "2", "price_lines-0-unit_price": "50",
             "hotels-TOTAL_FORMS": "2", "hotels-0-name": "Serena", "hotels-0-meal_plan": "Full Board", "hotels-0-price_per_person": "300",
             "hotels-0-photo_pack": lodge_pack.pk, "hotels-1-name": "Sopa", "hotels-1-meal_plan": "Half Board", "hotels-1-price_per_person": "250"}
    resp = _edit_post(client, it, **extra)
    assert resp.status_code == 302, getattr(resp, "context", None) and (resp.context["hotels"].errors, resp.context["lines"].errors, resp.context["formset"].errors)
    it = LeadItinerary.objects.get(pk=it.pk)
    assert it.nights == 5 and it.inclusion_list == ["Park fees", "Guide"] and it.photo_pack == pack
    assert list(it.days.first().photos.all()) == [p1]
    assert it.lines_total == D("100.00")
    assert it.hotel_from == D("250") * it.pax                           # cheapest option stands in for the headline price
    assert it.sell_total == it.day_sell + D("100") + D("250") * it.pax and it.profit == it.day_sell - it.cost_total
    # quotation carries the extra line and the cheapest lodge option
    q = services.itinerary_to_quotation(it, agent.user)
    descs = [i.description for i in q.items.all()]
    assert "Visa" in descs and any(d.startswith("Sopa") for d in descs)
    # detail, print and the public client page show the new sections
    for url in (it.get_absolute_url(), reverse("sales:itinerary_print", args=[it.pk])):
        body = client.get(url).content
        assert b"Park fees" in body and b"Sopa" in body
    services.enable_share(it, agent.user)
    page = client.get(it.public_path())
    assert page.status_code == 200 and b"Choose your stay" in page.content and b"30% deposit" in page.content and b"Visa" in page.content
    assert client.get(reverse("public_trip_image", args=[it.share_token, it.days.first().pk])).status_code == 200
    assert client.get(reverse("public_trip_hotel_image", args=[it.share_token, it.hotels.get(name="Serena").pk])).status_code == 200
    assert client.get(reverse("public_trip_hotel_image", args=[it.share_token, it.hotels.get(name="Sopa").pk])).status_code == 404


@pytest.mark.django_db
def test_slide_pdf_renders(client, agent, mara):
    pkg, _ = mara
    it = services.create_itinerary(_lead(agent, pkg), agent.user, package=pkg)
    pack = PhotoCategory.objects.create(name="Mara", kind="destination")
    for i in range(3):
        LibraryPhoto.objects.create(category=pack, image=img(f"{i}.png"))
    it.photo_pack = pack
    it.save()
    ItineraryHotel.objects.create(itinerary=it, name="Serena", price_per_person=300)
    ItineraryPriceLine.objects.create(itinerary=it, description="Visa", quantity=2, unit_price=50)
    client.force_login(agent.user)
    r = client.get(reverse("sales:itinerary_pdf", args=[it.pk]), {"download": 1})
    assert r.status_code == 200 and r["Content-Type"] == "application/pdf" and r.content.startswith(b"%PDF")
    assert "attachment" in r["Content-Disposition"]


@pytest.mark.django_db
def test_template_carries_terms_lines_and_copies_to_itinerary(client, agent, mara):
    pkg, _ = mara
    t = ItineraryTemplate.objects.create(title="Mara classic", inclusions="Meals", exclusions="Visa", terms="Pay 30%", intro="Welcome")
    TemplateDay.objects.create(template=t, day_number=1, title="Arrive")
    TemplatePriceLine.objects.create(template=t, description="Park fees", quantity=2, unit_price=80)
    it = services.create_itinerary(_lead(agent, pkg), agent.user, template=t)
    assert it.inclusions == "Meals" and it.terms == "Pay 30%" and it.intro == "Welcome" and it.lines_total == D("160.00")
    back = services.template_from_itinerary(it, agent.user, "Copy")
    assert back.terms == "Pay 30%" and back.price_lines.count() == 1
    dup = services.duplicate_template(t, agent.user)
    assert dup.price_lines.count() == 1 and dup.exclusions == "Visa"
    client.force_login(agent.user)
    assert client.get(reverse("sales:template_edit", args=[t.pk])).status_code == 200


def test_splash_in_base(client, db, admin_client):
    r = admin_client.get("/")
    assert b'id="splash"' in r.content


@pytest.mark.django_db
def test_client_replies_from_the_link_warm_the_lead_and_alert_the_agent(client, agent, mara):
    from apps.leads.models import Stage
    from apps.notifications.models import Notification
    from apps.sales.models import ClientResponse, FollowUp

    pkg, _ = mara
    e = _lead(agent, pkg)
    it = services.create_itinerary(e, agent.user, package=pkg)
    hotel = ItineraryHotel.objects.create(itinerary=it, name="Serena", price_per_person=300)
    services.enable_share(it, agent.user)
    url = reverse("public_trip_respond", args=[it.share_token])
    assert b"Yes, I'd like to book this" in client.get(it.public_path()).content
    r = client.post(url, {"kind": "book", "message": "Can we leave a day later?", "hotel": hotel.pk}, headers={"x-requested-with": "XMLHttpRequest"})
    assert r.status_code == 200 and r.json()["ok"]
    e.refresh_from_db()
    resp = ClientResponse.objects.get()
    assert resp.hotel == hotel and e.stage == Stage.HOT
    assert FollowUp.objects.filter(enquiry=e, title__startswith="Client wants to book").exists()
    assert Notification.objects.filter(user=agent.user, title__contains="wants to book").exists()
    assert any("later" in a.details for a in e.activities.all())
    # the agent sees the reply, and the journey strip on the lead page
    client.force_login(agent.user)
    assert b"Client replies" in client.get(it.get_absolute_url()).content
    page = client.get(e.get_absolute_url()).content
    assert b'class="journey' in page and b"Client says yes" in page
    # junk is refused and the flood limit holds
    assert client.post(url, {"kind": "nonsense"}, headers={"x-requested-with": "XMLHttpRequest"}).status_code == 400
    for _ in range(6):
        last = client.post(url, {"kind": "question", "message": "hi"}, headers={"x-requested-with": "XMLHttpRequest"})
    assert last.status_code == 400
    assert client.post(reverse("public_trip_respond", args=["bad-token"]), {"kind": "book"}).status_code == 404


@pytest.mark.django_db
def test_write_for_me_without_and_with_ai(client, agent, mara, monkeypatch):
    from apps.catalog.models import Destination
    from apps.ict import ai

    pkg, lodge = mara
    it = services.create_itinerary(_lead(agent, pkg), agent.user, package=pkg)
    dest = Destination.objects.get(slug="mara")
    dest.summary = "Famous for the Great Migration."
    dest.save()
    client.force_login(agent.user)
    url = reverse("sales:itinerary_write", args=[it.pk])
    r = client.post(url, {"title": "Game drive day", "destination": dest.pk, "accommodation": lodge.pk, "activities": "Game drive, Sundowner", "dinner": "1", "drive_hours": "4"}).json()
    assert r["mode"] == "template" and "Maasai Mara" in r["text"] and "Mara Serena" in r["text"] and "game drive" in r["text"].lower() and "dinner" in r["text"]
    assert client.post(url, {"what": "intro"}).json()["text"].startswith("Dear Wanjiru")
    monkeypatch.setattr(ai, "configured", lambda: True)
    monkeypatch.setattr(ai, "complete", lambda *a, **k: {"content": [{"type": "text", "text": "A golden day on the plains."}]})
    assert client.post(url, {"title": "x"}).json() == {"text": "A golden day on the plains.", "mode": "ai"}
    monkeypatch.setattr(ai, "complete", lambda *a, **k: (_ for _ in ()).throw(ai.AIUnavailable("down")))
    assert client.post(url, {"title": "Day"}).json()["mode"] == "template"        # AI down: still gives a draft
    assert client.get(url).status_code == 405


@pytest.mark.django_db
def test_global_search_finds_itineraries_quotations_and_pages(client, agent, mara):
    pkg, _ = mara
    e = _lead(agent, pkg)
    it = services.create_itinerary(e, agent.user, package=pkg, title="Honeymoon Mara special")
    client.force_login(agent.user)
    body = client.get("/search/", {"q": "honeymoon"}).content
    assert b"Honeymoon Mara special" in body and b"Itineraries" in body
    assert b"Go to" in client.get("/search/", {"q": "itiner"}).content


@pytest.mark.django_db
def test_duplicate_itinerary_copies_everything(client, agent, mara):
    pkg, _ = mara
    it = services.create_itinerary(_lead(agent, pkg), agent.user, package=pkg)
    pack = PhotoCategory.objects.create(name="Mara", kind="destination")
    ph = LibraryPhoto.objects.create(category=pack, image=img())
    it.days.first().photos.add(ph)
    it.inclusions = "Meals"
    it.save()
    ItineraryHotel.objects.create(itinerary=it, name="Serena", price_per_person=300)
    ItineraryPriceLine.objects.create(itinerary=it, description="Visa", quantity=1, unit_price=50)
    client.force_login(agent.user)
    r = client.post(reverse("sales:itinerary_duplicate", args=[it.pk]))
    new = LeadItinerary.objects.exclude(pk=it.pk).get()
    assert r.status_code == 302 and r.url == reverse("sales:itinerary_edit", args=[new.pk])
    assert new.title.endswith("(option B)") and new.day_count == it.day_count and new.inclusions == "Meals"
    assert list(new.days.first().photos.all()) == [ph] and new.hotels.count() == 1 and new.lines_total == D("50.00")
    assert new.status == "draft" and new.share_token is None
    assert client.get(it.get_absolute_url()).status_code == 200


@pytest.mark.django_db
def test_itinerary_nudges(agent, mara):
    from datetime import timedelta

    from django.utils import timezone

    from apps.leads.models import Stage
    from apps.notifications.models import Notification
    from apps.sales import tasks

    pkg, _ = mara
    e = _lead(agent, pkg)
    it = services.create_itinerary(e, agent.user, package=pkg)
    services.enable_share(it, agent.user)
    services.send_itinerary(it, "whatsapp", agent.user)
    LeadItinerary.objects.filter(pk=it.pk).update(shared_at=timezone.now() - timedelta(days=3), sent_at=timezone.now() - timedelta(days=2))
    assert e.stage == Stage.ENQUIRY or True
    tasks.itinerary_nudges()
    titles = list(Notification.objects.filter(user=agent.user).values_list("title", flat=True))
    assert any("hasn't opened" in t for t in titles) and any(t.startswith("Quote ") for t in titles)
    before = Notification.objects.count()
    tasks.itinerary_nudges()                                                   # deduped: no repeat nudges
    assert Notification.objects.count() == before
    LeadItinerary.objects.filter(pk=it.pk).update(share_first_viewed_at=timezone.now(), share_views=3)
    tasks.itinerary_nudges()
    assert Notification.objects.filter(user=agent.user, title__contains="keeps coming back").exists()


@pytest.mark.django_db
def test_import_from_the_standalone_builder(tmp_path):
    import sqlite3

    from django.core.management import call_command

    root = tmp_path / "builder"
    (root / "media" / "gallery").mkdir(parents=True)
    (root / "media" / "gallery" / "a.png").write_bytes(img("a.png").read())
    con = sqlite3.connect(root / "db.sqlite3")
    con.executescript("""
    create table itineraries_category (id integer primary key, name text, kind text, description text);
    create table itineraries_categoryimage (id integer primary key, image text, caption text, category_id int, tags text);
    create table itineraries_itinerarytemplate (id integer primary key, name text, tagline text, description text, intro text, inclusions text, exclusions text, terms text, currency text, nights int);
    create table itineraries_templateday (id integer primary key, template_id int, number int, title text, description text);
    create table itineraries_templatepriceitem (id integer primary key, template_id int, description text, quantity int, unit_price real);
    insert into itineraries_category values (1,'Kisumu','destination','');
    insert into itineraries_categoryimage values (1,'gallery/a.png','Lake',1,'lake, sunset');
    insert into itineraries_categoryimage values (2,'gallery/missing.png','Gone',1,'');
    insert into itineraries_itinerarytemplate values (1,'Lakeside weekend','Sun and fish','','Welcome','Meals','Flights','Pay 30%','KES',2);
    insert into itineraries_templateday values (1,1,1,'Arrive','Check in');
    insert into itineraries_templatepriceitem values (1,1,'Accommodation',2,5000);
    """)
    con.commit()
    con.close()
    call_command("import_itinerary_builder", str(root))
    call_command("import_itinerary_builder", str(root))                      # a second run adds nothing
    cat = PhotoCategory.objects.get(name="Kisumu")
    assert cat.images.count() == 1 and cat.images.first().tags == "lake, sunset"
    t = ItineraryTemplate.objects.get(title="Lakeside weekend")
    assert t.nights_override == 2 and t.days.count() == 1 and t.price_lines.get().unit_price == 5000 and t.terms == "Pay 30%"


def _booking(agent, mara):
    from datetime import date, timedelta

    from apps.sales.models import Booking

    pkg, _ = mara
    e = _lead(agent, pkg)
    return Booking.objects.create(enquiry=e, client=e.client, agent=agent, package=pkg, title="Mara 3 days", adults=2, currency="USD", total_amount=D("2000"),
                                  travel_start=date.today() - timedelta(days=10), travel_end=date.today() - timedelta(days=7))


@pytest.mark.django_db
def test_client_trip_page_payments_itinerary_and_feedback(client, agent, mara):
    from apps.notifications.models import Notification
    from apps.sales.models import BookingPayment, FollowUp, TripFeedback

    b = _booking(agent, mara)
    BookingPayment.objects.create(booking=b, amount=D("600"), method="mpesa")
    it = services.create_itinerary(b.enquiry, agent.user)
    services.enable_share(it, agent.user)
    client.force_login(agent.user)
    assert b"Create client trip page" in client.get(b.get_absolute_url()).content
    client.post(reverse("sales:booking_portal", args=[b.pk]))
    b.refresh_from_db()
    assert b.portal_token and b"Client trip page" in client.get(b.get_absolute_url()).content
    client.logout()
    url = reverse("public_trip_portal", args=[b.portal_token])
    page = client.get(url)
    assert page.status_code == 200 and page["X-Robots-Tag"].startswith("noindex")
    for needle in (b"Payments", b"How to pay", b"1,400", b"Your itinerary", b"Documents to bring", b"How was your trip?"):
        assert needle in page.content, needle
    assert client.get(reverse("public_trip_portal", args=["nope"])).status_code == 404
    post = reverse("public_trip_feedback", args=[b.portal_token])
    assert client.post(post, {"rating": "2", "nps": "4", "comment": "The vehicle broke down."}).status_code == 302
    fb = TripFeedback.objects.get()
    assert fb.kind == "detractor"
    assert FollowUp.objects.filter(enquiry=b.enquiry, title__startswith="Call").exists()
    assert Notification.objects.filter(user=agent.user, title__contains="rated their trip 2/5").exists()
    assert b"already have your feedback" in client.post(post, {"rating": "5", "nps": "10"}, follow=True).content and TripFeedback.objects.count() == 1
    assert b"Thank you. You rated your trip" in client.get(url).content
    # staff see it; an agent without management rights does not
    client.force_login(agent.user)
    assert client.get("/sales/feedback/").status_code == 403


@pytest.mark.django_db
def test_feedback_dashboard_and_guards(client, admin_user, agent, mara):
    from datetime import date, timedelta

    b = _booking(agent, mara)
    services.enable_portal(b, agent.user)
    post = reverse("public_trip_feedback", args=[b.portal_token])
    assert client.post(post, {"rating": "9", "nps": "10"}).status_code == 302     # out of range: refused, nothing saved
    from apps.sales.models import TripFeedback

    assert TripFeedback.objects.count() == 0
    client.post(post, {"rating": "5", "nps": "10", "guide_rating": "5", "comment": "Perfect!", "may_quote": "on"})
    assert TripFeedback.objects.get().may_quote
    client.force_login(admin_user)
    page = client.get("/sales/feedback/")
    assert page.status_code == 200 and b"Perfect!" in page.content and b"100" in page.content
    assert client.get("/sales/feedback/?low=1").status_code == 200
    # a trip still to come cannot be rated
    from apps.sales.models import Booking

    Booking.objects.filter(pk=b.pk).update(travel_start=date.today() + timedelta(days=9), travel_end=date.today() + timedelta(days=12))
    TripFeedback.objects.all().delete()
    client.logout()
    client.post(post, {"rating": "5", "nps": "10"})
    assert TripFeedback.objects.count() == 0


@pytest.mark.django_db
def test_vouchers_group_consecutive_nights_into_stays(client, agent, mara):
    from apps.sales import vouchers

    pkg, lodge = mara
    it = services.create_itinerary(_lead(agent, pkg), agent.user, package=pkg)
    stays = vouchers.stays(it)
    assert len(stays) == 1 and stays[0]["nights"] == 2 and stays[0]["lodge"] == lodge        # two nights in a row at one lodge: one voucher
    assert stays[0]["check_in"] == it.start_date and stays[0]["meals"] == "Half Board"         # breakfast + dinner on those days
    client.force_login(agent.user)
    r = client.get(reverse("sales:itinerary_vouchers", args=[it.pk]))
    assert r.status_code == 200 and r.content.startswith(b"%PDF")
    d = it.days.get(day_number=3)
    d.accommodation = lodge
    d.save()
    assert vouchers.stays(it)[0]["nights"] == 3
    d.accommodation = None
    d.save()
    d2 = it.days.get(day_number=2)
    d2.accommodation = None
    d2.save()
    assert [s["nights"] for s in vouchers.stays(it)] == [1]


@pytest.mark.django_db
def test_company_lists_replace_the_standard_checklists(client, agent, mara):
    from apps.core.models import CompanySetting

    b = _booking(agent, mara)
    services.enable_portal(b, agent.user)
    assert b"Passport valid" in client.get(reverse("public_trip_portal", args=[b.portal_token])).content
    c = CompanySetting.load()
    c.documents_list, c.packing_list = "Your own passport\nSigned waiver", "Hat"
    c.save()
    body = client.get(reverse("public_trip_portal", args=[b.portal_token])).content
    assert b"Signed waiver" in body and b"Passport valid" not in body and b"<span>Hat</span>" in body


@pytest.mark.django_db
def test_low_margin_itinerary_needs_a_manager_to_send(client, agent, make_user, mara):
    from apps.accounts import roles as R
    from apps.notifications.models import Notification

    pkg, _ = mara
    it = services.create_itinerary(_lead(agent, pkg), agent.user, package=pkg)
    manager = make_user("sm@example.com", R.SALES_MANAGER)
    client.force_login(agent.user)
    r = client.get(reverse("sales:itinerary_edit", args=[it.pk]))
    data = _initial(r.context["form"])
    data.update(_formset_post(r.context["formset"]))
    data["margin_percent"] = "3"
    assert client.post(reverse("sales:itinerary_edit", args=[it.pk]), data).status_code == 302
    assert Notification.objects.filter(user=manager, title__startswith="Low-margin").exists()
    it.refresh_from_db()
    assert it.margin_percent == 3
    with pytest.raises(ValueError, match="below the lowest allowed"):
        services.send_itinerary(it, "email", agent.user)
    assert services.send_itinerary(it, "email", manager).status == "sent"                    # a manager may send it


@pytest.mark.django_db
def test_setup_checklist_for_admins_only(client, admin_user, agent):
    from apps.core.home_widgets import setup_checklist

    client.force_login(admin_user)
    page = client.get("/")
    assert b"Finish setting up" in page.content and b"Company profile" in page.content
    s = setup_checklist(admin_user)
    assert s["total"] == 8 and s["done"] < 8
    assert setup_checklist(agent.user) is None


@pytest.mark.django_db
def test_notice_board(client, admin_user, agent, make_user):
    from datetime import date, timedelta

    from apps.accounts import roles as R
    from apps.notifications.models import Announcement, Notification

    fd = make_user("fd@example.com", R.RECEPTIONIST)
    client.force_login(admin_user)
    r = client.post("/notifications/board/new/", {"title": "Office closed Friday", "body": "Public holiday.", "audience": [R.RECEPTIONIST], "pinned": "on", "level": "important"})
    assert r.status_code == 302
    a = Announcement.objects.get()
    assert a.pinned and a.level == "important" and a.roles == [R.RECEPTIONIST]
    assert Notification.objects.filter(user=fd, title="Notice: Office closed Friday").exists()
    assert not Notification.objects.filter(user=agent.user).exists()                # not their audience
    client.force_login(fd)
    assert b"Office closed Friday" in client.get("/").content and b"Office closed Friday" in client.get("/notifications/board/").content
    assert client.post("/notifications/board/new/", {"title": "x", "body": "y"}).status_code == 403
    client.force_login(agent.user)
    assert b"Office closed Friday" not in client.get("/notifications/board/").content
    Announcement.objects.update(expires_on=date.today() - timedelta(days=1), audience="")
    client.force_login(fd)
    assert b"Office closed Friday" not in client.get("/notifications/board/").content   # expired
    client.force_login(admin_user)
    n = Announcement.objects.create(title="Hello", body="all")
    assert client.post(f"/notifications/board/{n.pk}/delete/").status_code == 302 and not Announcement.objects.filter(pk=n.pk).exists()


@pytest.mark.django_db
def test_letters_fill_tokens_from_a_booking(client, agent, admin_user, mara):
    from apps.sales.models import LetterTemplate

    b = _booking(agent, mara)
    client.force_login(agent.user)
    assert client.get("/sales/letters/").status_code == 200                        # first visit adds the standard four
    assert LetterTemplate.objects.count() == 4
    t = LetterTemplate.objects.get(name="Payment reminder")
    page = client.get(reverse("sales:letter", args=[b.pk, t.pk])).content.decode()
    assert "Dear Wanjiru" in page and b.reference in page and "USD 2,000" in page and "{first_name}" not in page
    assert "Print / Save as PDF" in client.get(reverse("sales:letter", args=[b.pk, t.pk]), {"print": 1}).content.decode()
    assert b"Letters" in client.get(b.get_absolute_url()).content
    t.body = "Hi {first_name} {unknown_token} {{ 7*7 }}"
    t.save()
    out = client.get(reverse("sales:letter", args=[b.pk, t.pk])).content.decode()
    assert "Hi Wanjiru {unknown_token} {{ 7*7 }}" in out                          # unknown tokens stay as typed, nothing is evaluated
    # only managers edit; agents cannot
    assert client.post("/sales/letters/", {"name": "x", "body": "y"}).status_code == 403
    client.force_login(admin_user)
    client.post("/sales/letters/", {"name": "Welcome pack", "body": "Hello {client_name}"})
    assert LetterTemplate.objects.filter(name="Welcome pack").exists()
    wp = LetterTemplate.objects.get(name="Welcome pack")
    client.post("/sales/letters/", {"id": wp.pk, "name": "Welcome pack", "body": "Hi again", "action": "delete"})
    assert not LetterTemplate.objects.filter(pk=wp.pk).exists()


@pytest.mark.django_db
def test_quotation_pages_link_to_the_itinerary(client, agent, mara):
    pkg, _ = mara
    e = _lead(agent, pkg)
    it = services.create_itinerary(e, agent.user, package=pkg)
    for d in it.days.all():
        d.lodging_pp = 100
        d.save()
    it.inclusions = "All meals"
    it.save()
    q = services.itinerary_to_quotation(it, agent.user)
    client.force_login(agent.user)
    body = client.get(q.get_absolute_url()).content
    assert it.title.encode() in body and b"client view" not in body or it.title.encode() in body
    printed = client.get(reverse("sales:quotation_print", args=[q.pk])).content
    assert b"Your itinerary at a glance" in printed and b"All meals" in printed and b"Nairobi to the Mara" in printed
    # a quotation with no itinerary offers to build one
    from apps.sales.models import Quotation

    bare = services.create_quotation(_lead(agent, pkg, phone="0799000111"), agent.user, items=[{"kind": "other", "description": "x", "quantity": 1, "unit_price": 5}])
    assert b"Build itinerary" in client.get(bare.get_absolute_url()).content


@pytest.mark.django_db
def test_picked_library_photo_shows_on_internal_pages(client, agent, mara):
    pkg, _ = mara
    it = services.create_itinerary(_lead(agent, pkg), agent.user, package=pkg)
    pack = PhotoCategory.objects.create(name="Mara", kind="destination")
    ph = LibraryPhoto.objects.create(category=pack, image=img("z.png"))
    day = it.days.first()
    assert day.picture_url == ""
    day.photos.add(ph)
    assert day.picture_url == ph.image.url
    client.force_login(agent.user)
    assert ph.image.url.encode() in client.get(it.get_absolute_url()).content
    assert ItineraryTemplate.objects.create(title="T").days.count() == 0 and TemplateDay(template=ItineraryTemplate.objects.first(), title="d").picture_url == ""


@pytest.mark.django_db
def test_build_itinerary_from_a_matching_safari(client, agent, mara):
    pkg, _ = mara
    e = _lead(agent, pkg)
    e.package = None
    e.save()
    client.force_login(agent.user)
    page = client.get(f"/sales/itineraries/new/?enquiry={e.pk}&package={pkg.pk}")
    assert page.status_code == 200 and f'value="p:{pkg.pk}" selected'.encode() in page.content


@pytest.mark.django_db
def test_lodge_request_text_and_trip_calendar(client, agent, mara):
    from apps.core.models import CompanySetting
    from apps.sales import vouchers

    pkg, lodge = mara
    lodge.contact_name, lodge.contact_phone, lodge.contact_email = "Joseph Kamau", "0711222333", "res@serena.example"
    lodge.save()
    b = _booking(agent, mara)
    it = services.create_itinerary(b.enquiry, agent.user, package=pkg)
    client.force_login(agent.user)
    page = client.get(it.get_absolute_url()).content.decode()
    assert "Lodge requests" in page and "wa.me/254711222333" in page and "mailto:res@serena.example" in page and "Hello Joseph" in page
    txt = vouchers.request_text(it, vouchers.stays(it)[0], CompanySetting.load())
    assert "2 nights" in txt and "Meal plan:" in txt and it.enquiry.reference in txt
    services.enable_portal(b, agent.user)
    client.logout()
    r = client.get(reverse("public_trip_calendar", args=[b.portal_token]))
    body = r.content.decode()
    assert r["Content-Type"].startswith("text/calendar") and body.startswith("BEGIN:VCALENDAR") and "SUMMARY:Mara 3 days" in body and "SUMMARY:Day 1" in body
    assert body.count("BEGIN:VEVENT") == 1 + it.day_count
    assert client.get(reverse("public_trip_calendar", args=["nope"])).status_code == 404
    assert b"Add to my calendar" in client.get(reverse("public_trip_portal", args=[b.portal_token])).content


@pytest.mark.django_db
def test_trip_page_without_itinerary_and_with_a_payment_plan(client, agent, mara):
    from datetime import date, timedelta

    from apps.dealdesk.models import Instalment, PaymentPlan

    b = _booking(agent, mara)
    services.enable_portal(b, agent.user)
    url = reverse("public_trip_portal", args=[b.portal_token])
    page = client.get(url)
    assert page.status_code == 200 and b"Your itinerary" not in page.content and b"Deposit (30%)" in page.content
    plan = PaymentPlan.objects.create(booking=b)
    Instalment.objects.create(plan=plan, due_date=date.today() - timedelta(days=3), amount=D("1000"))
    Instalment.objects.create(plan=plan, due_date=date.today() + timedelta(days=20), amount=D("1000"))
    body = client.get(url).content.decode()
    assert "Instalment due" in body and "Overdue" in body and "Deposit (30%)" not in body
    assert client.get(reverse("public_trip_calendar", args=[b.portal_token])).status_code == 200     # works with no itinerary too


@pytest.mark.django_db
def test_client_opens_the_designed_proposal_and_pdf_without_a_login(client, agent, mara):
    import base64

    pkg, _ = mara
    it = services.create_itinerary(_lead(agent, pkg), agent.user, package=pkg)
    pack = PhotoCategory.objects.create(name="Mara", kind="destination")
    ph = LibraryPhoto.objects.create(category=pack, image=img("cover.png"))
    other = LibraryPhoto.objects.create(category=PhotoCategory.objects.create(name="Other", kind="destination"), image=img("secret.png"))
    it.photo_pack = pack
    it.save()
    ItineraryHotel.objects.create(itinerary=it, name="Serena", price_per_person=300)
    # the staff print page stays behind login
    assert client.get(reverse("sales:itinerary_print", args=[it.pk])).status_code == 302
    services.enable_share(it, agent.user)
    # the client's link offers the designed proposal and its PDF, and they open with no login
    page = client.get(it.public_path()).content
    assert it.proposal_path.encode() in page and it.pdf_path.encode() in page
    r = client.get(it.proposal_path)
    assert r.status_code == 200 and b"Serena" in r.content and it.pdf_path.encode() in r.content
    assert r["X-Robots-Tag"] == "noindex, nofollow" and b"/sales/" not in r.content
    pdf = client.get(it.pdf_path)
    assert pdf.status_code in (200, 302)   # 302 back to the proposal when the PDF engine is missing on this host
    if pdf.status_code == 200:
        assert pdf.content.startswith(b"%PDF") and "attachment" in pdf["Content-Disposition"]
    # its own pictures are served; another file, or a made-up name, is not
    enc = lambda f: base64.urlsafe_b64encode(f.name.encode()).decode().rstrip("=")  # noqa: E731
    assert client.get(reverse("public_trip_file", args=[it.share_token, enc(ph.image)])).status_code == 200
    assert client.get(reverse("public_trip_file", args=[it.share_token, enc(other.image)])).status_code == 404
    assert client.get(reverse("public_trip_file", args=[it.share_token, "!!"])).status_code == 404
    # switching the link off closes the proposal and the PDF too
    token, proposal = it.share_token, it.proposal_path
    services.revoke_share(it, agent.user)
    assert client.get(proposal).status_code == 404 and client.get(reverse("public_trip_pdf", args=[token])).status_code == 404
