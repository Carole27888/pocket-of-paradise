"""Sales itinerary builder: copy from package, catalogue lodge rates, pricing/margin, quotation, send, scoping."""
from datetime import date, timedelta
from decimal import Decimal as D

import pytest
from django.urls import reverse

from apps.catalog.models import Accommodation, Destination, Itinerary, ItineraryDay, RoomRate, TourPackage
from apps.leads import services as lead_services
from apps.leads.models import Stage
from apps.sales import services
from apps.sales.models import LeadItinerary, QuotationStatus


@pytest.fixture
def mara(db):
    dest = Destination.objects.create(name="Maasai Mara", slug="mara")
    lodge = Accommodation.objects.create(name="Mara Serena", destination=dest)
    RoomRate.objects.create(accommodation=lodge, room_type="Double / Twin", season="high", rate_per_person=D("300"), currency="USD",
                            valid_from=date(2026, 7, 1), valid_to=date(2026, 10, 31))
    RoomRate.objects.create(accommodation=lodge, room_type="Double / Twin", season="low", rate_per_person=D("180"), currency="USD",
                            valid_from=date(2026, 11, 1), valid_to=date(2027, 6, 30))
    itin = Itinerary.objects.create(title="Mara 3 days")
    for n, (title, acc) in enumerate([("Nairobi to the Mara", lodge), ("Full day game drives", lodge), ("Return to Nairobi", None)], start=1):
        ItineraryDay.objects.create(itinerary=itin, day_number=n, title=title, accommodation=acc, destination=dest, breakfast=True,
                                    dinner=acc is not None, activities="Game drive" if n == 2 else "", drive_km=270 if n != 2 else None)
    pkg = TourPackage.objects.create(title="Mara 3 days", days=3, nights=2, currency="USD", price_per_person=D("900"),
                                     status="published", itinerary=itin, summary="Big cats and the Mara plains.")
    return pkg, lodge


def _lead(agent, pkg, phone="0712345678", adults=2, children=0, travel=date(2026, 8, 10)):
    e = lead_services.create_enquiry(client_data={"full_name": "Wanjiru Kamau", "phone": phone},
                                     enquiry_data={"source": "whatsapp", "package": pkg, "adults": adults, "children": children,
                                                   "travel_date_from": travel}, auto_assign=False)
    lead_services.assign_agent(e, agent, None, "manual")
    return e


@pytest.mark.django_db
def test_create_copies_package_days_and_applies_dated_lodge_rate(agent, mara):
    pkg, lodge = mara
    e = _lead(agent, pkg)
    e.client.is_international = False
    it = services.create_itinerary(e, agent.user)
    assert it.day_count == 3 and it.currency == "KES" and it.start_date == date(2026, 8, 10) and it.intro.startswith("Big cats")
    day1 = it.days.get(day_number=1)
    assert day1.accommodation == lodge and day1.room_type == "Double / Twin" and day1.breakfast
    # high-season rate (USD 300) converted to KES at the stored rate
    assert day1.lodging_pp == (D("300") * services.fx_rate("USD") / services.fx_rate("KES")).quantize(D("0.01"))
    assert it.days.get(day_number=3).lodging_pp == 0                    # no lodge on the return day


@pytest.mark.django_db
def test_rate_picks_season_by_date_and_converts_currency(mara):
    pkg, lodge = mara
    assert services.suggest_rate(lodge, date(2026, 8, 1), "USD") == (D("300.00"), "Double / Twin")
    assert services.suggest_rate(lodge, date(2026, 12, 1), "USD")[0] == D("180.00")
    kes = services.suggest_rate(lodge, date(2026, 8, 1), "KES")[0]
    assert kes == (D("300") * services.fx_rate("USD")).quantize(D("0.01"))
    empty = Accommodation.objects.create(name="No rates", destination=lodge.destination)
    assert services.suggest_rate(empty, date(2026, 8, 1)) == (None, "")


@pytest.mark.django_db
def test_totals_margin_and_quotation_lines(agent, mara):
    pkg, lodge = mara
    e = _lead(agent, pkg, adults=2, children=1)                         # 3 guests
    it = services.create_itinerary(e, agent.user)
    it.currency, it.margin_percent = "USD", D("20")
    it.save()
    for d in it.days.all():
        d.lodging_pp = D("300") if d.accommodation_id else D("0")
        d.activities_pp = D("50") if d.day_number == 2 else D("0")
        d.transport = D("200") if d.drive_km else D("0")
        d.save()
    # lodging 2 nights x 300 x 3 pax = 1800; activities 50 x 3 = 150; transport 200 x 2 days = 400  -> cost 2350
    it.refresh_from_db()
    assert it.pax == 3 and it.cost_total == D("2350.00") and it.sell_total == D("2820.00") and it.profit == D("470.00")
    assert it.sell_pp == D("940.00")
    q = services.itinerary_to_quotation(it, agent.user)
    assert q.status == QuotationStatus.DRAFT and q.currency == "USD" and q.items.count() == 3
    lodging = q.items.get(kind="accommodation")
    assert lodging.quantity == D("6") and lodging.unit_price == D("360.00") and "2 nights" in lodging.description
    assert q.subtotal == D("2820.00")                                   # margin carried into the client price
    it.refresh_from_db()
    assert it.quotation == q


@pytest.mark.django_db
def test_quotation_requires_a_price_and_send_moves_stage(agent, mara):
    pkg, _ = mara
    e = _lead(agent, pkg)
    it = LeadItinerary.objects.create(enquiry=e, title="Blank", currency="USD")
    with pytest.raises(ValueError, match="Price the itinerary"):
        services.itinerary_to_quotation(it, agent.user)
    with pytest.raises(ValueError, match="at least one day"):
        services.send_itinerary(it, "whatsapp", agent.user)
    it2 = services.create_itinerary(e, agent.user)
    services.send_itinerary(it2, "whatsapp", agent.user)
    e.refresh_from_db(); it2.refresh_from_db()
    assert it2.status == "sent" and e.stage == Stage.ITINERARY and e.last_contact_at
    services.send_itinerary(it2, "email", agent.user)                   # already past the stage: no regression
    e.refresh_from_db()
    assert e.stage == Stage.ITINERARY


@pytest.mark.django_db
def test_builder_views_and_scoping(client, agent, other_agent, mara):
    pkg, lodge = mara
    mine, theirs = _lead(agent, pkg), _lead(other_agent, pkg, phone="0722000000")
    client.force_login(agent.user)
    r = client.post(f"/sales/itineraries/new/?enquiry={mine.pk}", {"enquiry": mine.pk, "start": f"p:{pkg.pk}", "title": ""})
    it = LeadItinerary.objects.get(enquiry=mine)
    assert r.status_code == 302 and r.url == reverse("sales:itinerary_edit", args=[it.pk])
    for url in ("/sales/itineraries/", it.get_absolute_url(), f"/sales/itineraries/{it.pk}/edit/", f"/sales/itineraries/{it.pk}/print/",
                f"/sales/itineraries/{it.pk}/pdf/", f"/sales/itineraries/new/?enquiry={mine.pk}", mine.get_absolute_url()):
        resp = client.get(url)
        assert resp.status_code in (200, 302), (url, resp.status_code)
        if resp.status_code == 200 and "text/html" in resp["Content-Type"]:
            assert b"{{" not in resp.content and b"{%" not in resp.content, url
    # other agent's lead and itinerary are off limits
    assert client.get(f"/sales/itineraries/new/?enquiry={theirs.pk}").status_code == 404
    other = services.create_itinerary(theirs, other_agent.user)
    assert client.get(other.get_absolute_url()).status_code == 404
    assert client.get(f"/sales/itineraries/{other.pk}/edit/").status_code == 404
    # rate endpoint
    j = client.get("/sales/itineraries/rate/", {"accommodation": lodge.pk, "date": "2026-08-01", "currency": "USD"}).json()
    assert j["rate"] == "300.00" and j["room_type"] == "Double / Twin"
    assert client.get("/sales/itineraries/rate/", {"accommodation": 0}).json()["rate"] is None


@pytest.mark.django_db
def test_editing_renumbers_days_and_removes_deleted(client, agent, mara):
    pkg, lodge = mara
    e = _lead(agent, pkg)
    it = services.create_itinerary(e, agent.user)
    days = list(it.days.all())
    data = {"title": "Edited", "start_date": "2026-08-10", "currency": "USD", "margin_percent": "25", "intro": "Hi",
            "days-TOTAL_FORMS": "3", "days-INITIAL_FORMS": "3", "days-MIN_NUM_FORMS": "1", "days-MAX_NUM_FORMS": "1000"}
    for i, d in enumerate(days):
        data.update({f"days-{i}-id": d.pk, f"days-{i}-itinerary": it.pk, f"days-{i}-title": d.title, f"days-{i}-lodging_pp": "100",
                     f"days-{i}-activities_pp": "", f"days-{i}-transport": ""})
    data["days-1-DELETE"] = "on"
    client.force_login(agent.user)
    r = client.post(f"/sales/itineraries/{it.pk}/edit/", data)
    assert r.status_code == 302, getattr(r, "context", None) and r.context["formset"].errors
    it.refresh_from_db()
    assert it.title == "Edited" and it.margin_percent == D("25") and [d.day_number for d in it.days.all()] == [1, 2]
    assert it.cost_total == D("2") * D("100") * 2                       # two remaining days, 2 guests, lodging 100


@pytest.mark.django_db
def test_director_is_read_only_on_itineraries(client, agent, make_user, mara):
    from apps.accounts import roles as R

    pkg, _ = mara
    it = services.create_itinerary(_lead(agent, pkg), agent.user)
    client.force_login(make_user("dir@example.com", R.DIRECTOR))
    assert client.get(it.get_absolute_url()).status_code == 200
    assert client.get(f"/sales/itineraries/{it.pk}/edit/").status_code == 403
    assert client.post(f"/sales/itineraries/{it.pk}/send/", {"via": "email"}).status_code == 403


@pytest.mark.django_db
def test_each_day_can_have_a_picture(client, agent, mara, settings, tmp_path):
    import io

    from django.core.files.uploadedfile import SimpleUploadedFile
    from PIL import Image

    settings.MEDIA_ROOT = tmp_path
    pkg, _ = mara
    e = _lead(agent, pkg)
    it = services.create_itinerary(e, agent.user, package=pkg)
    client.force_login(agent.user)
    day = it.days.first()

    def png(size=(40, 30)):
        buf = io.BytesIO(); Image.new("RGB", size, "orange").save(buf, "PNG")
        return SimpleUploadedFile("day1.png", buf.getvalue(), content_type="image/png")

    r = client.get(reverse("sales:itinerary_edit", args=[it.pk]))
    form = r.context["formset"]
    data = {k: v for k, v in _initial(r.context["form"]).items()}
    data.update(_formset_post(form))
    data[f"days-0-image"] = png()
    resp = client.post(reverse("sales:itinerary_edit", args=[it.pk]), data)
    assert resp.status_code == 302, getattr(resp, "context", None) and resp.context["formset"].errors
    day.refresh_from_db()
    assert day.image and day.picture_url.startswith("/media/itinerary/days/")
    assert b'class="itin-photo"' in client.get(it.get_absolute_url()).content
    assert day.picture_url.encode() in client.get(reverse("sales:itinerary_print", args=[it.pk])).content


def _initial(form):
    out = {}
    for name, f in form.fields.items():
        v = form.initial.get(name, f.initial)
        if v is not None and v is not False:
            out[name] = str(v)
    return out


def _formset_post(fs):
    out = {f"{fs.prefix}-TOTAL_FORMS": str(len(fs.forms)), f"{fs.prefix}-INITIAL_FORMS": str(len(fs.forms)),
           f"{fs.prefix}-MIN_NUM_FORMS": "1", f"{fs.prefix}-MAX_NUM_FORMS": "1000"}
    for i, f in enumerate(fs.forms):
        for name, field in f.fields.items():
            if name in ("image", "photos"):
                continue
            v = f.initial.get(name, field.initial)
            key = f"{fs.prefix}-{i}-{name}"
            if v is None or v is False:
                continue
            out[key] = "on" if v is True else str(getattr(v, "pk", v))
        out[f"{fs.prefix}-{i}-id"] = str(f.instance.pk)
        out[f"{fs.prefix}-{i}-itinerary"] = str(f.instance.itinerary_id)
    return out


@pytest.mark.django_db
def test_catalogue_photos_are_public_but_other_uploads_need_login(client, settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path
    (tmp_path / "packages").mkdir(); (tmp_path / "packages" / "a.jpg").write_bytes(b"x")
    (tmp_path / "itinerary").mkdir(); (tmp_path / "itinerary" / "b.jpg").write_bytes(b"x")
    assert client.get("/media/packages/a.jpg").status_code == 200
    r = client.get("/media/itinerary/b.jpg")
    assert r.status_code == 302 and "login" in r["Location"]


@pytest.mark.django_db
def test_client_share_link_public_page(client, agent, mara, make_user):
    from apps.notifications.models import Notification

    pkg, _ = mara
    it = services.create_itinerary(_lead(agent, pkg), agent.user, package=pkg)
    assert client.get("/trip/nope/").status_code == 404
    client.force_login(agent.user)
    assert client.post(reverse("sales:itinerary_share", args=[it.pk])).status_code == 302
    it.refresh_from_db()
    assert it.share_token and f"/trip/{it.share_token}/" in client.get(it.get_absolute_url()).content.decode()
    client.logout()
    r = client.get(f"/trip/{it.share_token}/")
    html = r.content.decode()
    assert r.status_code == 200 and "noindex" in r["X-Robots-Tag"] and it.title in html and 'id="pay-open"' in html and "M-PESA" in html and "Crypto" in html
    assert "Margin" not in html and "Cost to us" not in html                                # internal pricing never leaks
    it.refresh_from_db()
    assert it.share_views == 1 and Notification.objects.filter(user=agent.user, title__contains="opened the itinerary").exists()
    other = make_user("other@example.com", "Sales Agent")
    client.force_login(other)
    assert client.post(reverse("sales:itinerary_share", args=[it.pk]), {"action": "revoke"}).status_code in (403, 404)
    client.force_login(agent.user)
    client.post(reverse("sales:itinerary_share", args=[it.pk]), {"action": "revoke"})
    client.logout()
    assert client.get(f"/trip/{it.share_token or 'x'}/").status_code == 404


@pytest.mark.django_db
def test_public_page_pictures_are_served_by_token_only(client, agent, mara, settings, tmp_path):
    import io

    from django.core.files.uploadedfile import SimpleUploadedFile
    from PIL import Image

    settings.MEDIA_ROOT = tmp_path
    pkg, _ = mara
    it = services.create_itinerary(_lead(agent, pkg), agent.user, package=pkg)
    day = it.days.first()
    buf = io.BytesIO(); Image.new("RGB", (30, 20), "red").save(buf, "PNG")
    day.image.save("d.png", SimpleUploadedFile("d.png", buf.getvalue(), content_type="image/png")); day.save()
    services.enable_share(it)
    url = f"/trip/{it.share_token}/img/{day.pk}/"
    assert client.get(url).status_code == 200 and client.get(url)["Content-Type"] == "image/png"
    assert url in client.get(f"/trip/{it.share_token}/").content.decode()
    assert client.get(f"/trip/wrongtoken/img/{day.pk}/").status_code == 404
    assert client.get(day.image.url).status_code == 302          # the raw upload still needs a sign-in


@pytest.mark.django_db
def test_templates_library_and_dragged_days(client, agent, other_agent, mara, make_user):
    from apps.sales.models import ItineraryTemplate, TemplateDay

    pkg, lodge = mara
    client.force_login(agent.user)
    it = services.create_itinerary(_lead(agent, pkg), agent.user, package=pkg)
    # save as template, then it shows in the list and can be reused for another lead
    r = client.post(reverse("sales:itinerary_save_template", args=[it.pk]), {"title": "Mara classic"})
    t = ItineraryTemplate.objects.get(title="Mara classic")
    assert r.status_code == 302 and t.day_count == 3 and t.created_by_id == agent.user.pk
    assert b"Mara classic" in client.get(reverse("sales:templates")).content
    e2 = _lead(agent, pkg, phone="0722000999")
    r = client.post(reverse("sales:itinerary_new"), {"enquiry": e2.pk, "start": f"t:{t.pk}", "title": ""})
    new = LeadItinerary.objects.get(enquiry=e2)
    assert [d.title for d in new.days.all()] == [d.title for d in t.days.all()] and ItineraryTemplate.objects.get(pk=t.pk).times_used == 1
    # the builder pages carry the library and the drag handles
    html = client.get(reverse("sales:itinerary_edit", args=[new.pk])).content.decode()
    assert "tpl-library" in html and "lib-day" in html and "day-grip" in html and 'name="days-0-day_number"' in html
    # a day dropped in (client side) arrives as a normal form row at the chosen position with day_number following screen order
    day7 = t.days.last()
    page = client.get(reverse("sales:itinerary_edit", args=[new.pk]))
    data = {k: v for k, v in _initial(page.context["form"]).items()}
    data.update(_formset_post(page.context["formset"]))
    n = len(page.context["formset"].forms)
    data.update({f"days-{i}-day_number": str(i + 1 if i < 1 else i + 2) for i in range(n)})      # existing days shift down one
    data["days-TOTAL_FORMS"] = str(n + 1)
    data.update({f"days-{n}-day_number": "2", f"days-{n}-title": "Borrowed day", f"days-{n}-destination": str(day7.destination_id), f"days-{n}-copy_image_from": f"t:{day7.pk}"})
    resp = client.post(reverse("sales:itinerary_edit", args=[new.pk]), data)
    assert resp.status_code == 302
    assert [d.title for d in new.days.order_by("day_number")][1] == "Borrowed day" and new.days.count() == 4
    # anyone in sales can edit and duplicate a shared template; only its owner or a manager deletes it
    client.force_login(other_agent.user)
    assert client.get(reverse("sales:template_edit", args=[t.pk])).status_code == 200
    assert client.post(reverse("sales:template_delete", args=[t.pk])).status_code == 403
    assert client.post(reverse("sales:template_duplicate", args=[t.pk])).status_code == 302 and ItineraryTemplate.objects.filter(title="Mara classic (copy)").exists()
    client.force_login(agent.user)
    page = client.get(reverse("sales:template_edit", args=[t.pk]))
    assert page.status_code == 200 and "tpl-library" in page.content.decode()


@pytest.mark.django_db
def test_template_detail_page(client, agent, mara):
    pkg, _ = mara
    it = services.create_itinerary(_lead(agent, pkg), agent.user, package=pkg)
    t = services.template_from_itinerary(it, agent.user, "Detail me")
    client.force_login(agent.user)
    r = client.get(t.get_absolute_url())
    html = r.content.decode()
    assert r.status_code == 200 and "Day by day" in html and "Mara Serena" in html and "Use for a lead" in html and "At a glance" in html
    assert t.get_absolute_url() in client.get(reverse("sales:templates")).content.decode()


@pytest.mark.django_db
def test_new_itinerary_title_defaults_to_client_and_interest(client, agent, mara):
    pkg, _ = mara
    e = _lead(agent, pkg)
    client.force_login(agent.user)
    html = client.get(f"/sales/itineraries/new/?enquiry={e.pk}").content.decode()
    assert f"{e.client.full_name} · {e.interest_label}" in html                      # pre-filled when opened from a lead
    assert 'data-autotitle-source' in client.get("/sales/itineraries/new/").content.decode()   # filled by the page when you pick one
    client.post("/sales/itineraries/new/", {"enquiry": e.pk, "start": "", "title": ""})
    assert LeadItinerary.objects.get(enquiry=e).title == f"{e.client.full_name} · {e.interest_label}"


@pytest.mark.django_db
def test_delete_itinerary_rules(client, agent, other_agent, mara, make_user):
    pkg, _ = mara
    e = _lead(agent, pkg)
    draft = services.create_itinerary(e, agent.user, package=pkg)
    sent = services.create_itinerary(e, agent.user, package=pkg, title="Sent one")
    services.send_itinerary(sent, "whatsapp", agent.user)
    client.force_login(agent.user)
    html = client.get(reverse("sales:itineraries")).content.decode()
    assert reverse("sales:itinerary_delete", args=[draft.pk]) in html and reverse("sales:itinerary_delete", args=[sent.pk]) not in html
    assert client.post(reverse("sales:itinerary_delete", args=[sent.pk])).status_code == 403           # sent ones stay on record
    client.force_login(other_agent.user)
    assert client.post(reverse("sales:itinerary_delete", args=[draft.pk])).status_code == 404         # not their lead
    client.force_login(agent.user)
    assert client.post(reverse("sales:itinerary_delete", args=[draft.pk])).status_code == 302
    assert not LeadItinerary.objects.filter(pk=draft.pk).exists()
    mgr = make_user("sm@example.com", "Sales Manager")
    client.force_login(mgr)
    assert client.post(reverse("sales:itinerary_delete", args=[sent.pk])).status_code == 302          # a manager may remove a sent one
