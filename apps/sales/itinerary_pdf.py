"""Client itinerary as a landscape, slide-style PDF (cover, day-by-day grid, one page per lodge option, pricing, payment).
Ported from the standalone Itinerary Builder; the page-fitting maths picks the biggest photos and text that keep the journey on the fewest pages."""
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.template.loader import render_to_string

ACCENT, ACCENT2 = "#1F6F6B", "#EDA036"   # logo sea teal (deepened) and sun orange

CARD_TEXT_WIDTH_PT = {3: 226, 4: 164}
FOUR_COL_FROM = 7   # trips with this many days or more use four cards across, so the last page is not left with one orphan card
FIRST_PAGE_MM, NEXT_PAGE_MM = 158, 182
MIN_PHOTO_MM, MAX_PHOTO_MM = 22, 74
SHORT_TRIP_DAYS = 6
LONG_TRIP_PHOTO_MM = 30
FONTS_PT = (10.0, 9.2, 8.6, 8.1, 7.8, 7.5)


def _uri(file, web=False) -> str:
    """A file the PDF engine can read from disk (file://); for the staff on-screen view the picture's web address; and for the client's
    public proposal (`web` is the link token) an address that works without a login and only for that itinerary's own pictures."""
    try:
        if web:
            Path(file.path).stat()          # only offer pictures that really exist
            if isinstance(web, str):
                import base64

                from django.urls import reverse

                return reverse("public_trip_file", args=[web, base64.urlsafe_b64encode(file.name.encode()).decode().rstrip("=")])
            return file.url
        return Path(file.path).as_uri()
    except Exception:   # the file is missing on disk or storage is remote
        return ""


def shared_file_names(it) -> set:
    """Every picture this itinerary's client proposal may show: the only files its public link is allowed to serve."""
    names = set()
    for d in it.days.all():
        names.update(f.name for f in d.gallery_files)
    if it.photo_pack_id:
        names.update(p.image.name for p in it.photo_pack.images.all())
    for h in it.hotels.select_related("photo_pack", "accommodation__photo_pack"):
        if h.pack:
            names.update(p.image.name for p in h.pack.images.all())
    return names


def _cycle(items, n):
    return [items[i % len(items)] for i in range(n)] if items else []


def _card_height_mm(title, desc, img_h, font, cols=3):
    cpl = CARD_TEXT_WIDTH_PT[cols] / (font * 0.5)
    lines = sum(max(1, -(-len(p) // int(cpl))) for p in desc.split("\n")) if desc else 0
    title_lines = max(1, -(-len(title) // (34 if cols == 3 else 24)))
    return (img_h + 14 + title_lines * 5.2 + lines * font * 1.42 * 0.3528) * 1.03


def _pages_needed(days, img_h, font, cols=3):
    rows = [days[i:i + cols] for i in range(0, len(days), cols)]
    used, page, cap = 0.0, 1, FIRST_PAGE_MM
    for r in rows:
        h = max(_card_height_mm(e["day"].title, e["day"].story, img_h if e["photos"] else 0, font, cols) for e in r) + 4
        if used + h > cap:
            page, used, cap = page + 1, 0.0, NEXT_PAGE_MM
        used += h
    return page


def fit_layout(days, cols=3):
    """Biggest photos (then biggest text) that still reach the fewest possible pages."""
    if not days:
        return MAX_PHOTO_MM, FONTS_PT[0]
    short = len(days) <= SHORT_TRIP_DAYS
    fonts = FONTS_PT[:4] if short else FONTS_PT
    fewest = _pages_needed(days, MIN_PHOTO_MM, fonts[-1], cols)
    biggest = MAX_PHOTO_MM if short else LONG_TRIP_PHOTO_MM
    for img_h in range(biggest, MIN_PHOTO_MM - 1, -2):
        for font in fonts:
            if _pages_needed(days, img_h, font, cols) <= fewest:
                return img_h, font
    return MIN_PHOTO_MM, fonts[-1]


def build_context(it, company, web=False) -> dict:
    pack_files = [p.image for p in it.photo_pack.images.all()][::-1] if it.photo_pack_id else []
    pack_uris = [u for u in (_uri(f, web) for f in pack_files) if u]
    day_objs = list(it.days.select_related("destination").prefetch_related("photos"))
    days = []
    for i, d in enumerate(day_objs):
        files = d.gallery_files
        photos = [u for u in (_uri(f, web) for f in files[:2]) if u]
        if not photos and pack_uris:   # nothing picked: borrow from the pack so the page isn't bare
            photos = [pack_uris[i % len(pack_uris)]]
        days.append({"day": d, "photos": photos, "date": it.start_date + timedelta(days=max(d.day_number - 1, 0)) if it.start_date else None})
    cover = _cycle(pack_uris or [p for e in days for p in e["photos"]], 3)

    hotels = []
    for h in it.hotels.select_related("photo_pack", "accommodation__photo_pack"):
        photos = [u for u in (_uri(p.image, web) for p in list(h.pack.images.all())[::-1][:5]) if u] if h.pack else []
        hotels.append({"h": h, "photos": photos, "total": h.total})
    # a lodge with no photos of its own borrows the trip's pictures so its page is never a blank panel
    spare = list(dict.fromkeys([p for e in days for p in e["photos"]] + pack_uris))
    for x in hotels:
        if not x["photos"] and spare:
            x["photos"] = spare[:3] if len(spare) >= 3 else spare[:1]

    prices = []
    if it.day_sell:
        prices.append({"description": f"Safari package: {it.day_count} day{'s' if it.day_count != 1 else ''} (lodging, activities, vehicle and guide)",
                       "quantity": 1, "unit_price": it.day_sell, "line_total": it.day_sell})
    prices += [{"description": p.description, "quantity": p.quantity, "unit_price": p.unit_price, "line_total": p.line_total} for p in it.price_lines.all()]
    prices += _travel_price_row(it)

    cols = 4 if len(days) >= FOUR_COL_FROM else 3
    img_h, font_pt = fit_layout([{**e, "photos": e["photos"][:2]} for e in days], cols)
    per_day = 2 if img_h >= 40 else 1
    for e in days:
        e["photos"] = e["photos"][:per_day]
    rows = [days[i:i + cols] for i in range(0, len(days), cols)]
    for r in rows:
        r.extend([None] * (cols - len(r)))
    has_hotel_prices = any(x["h"].price_per_person for x in hotels)
    total = it.day_sell + it.lines_total + it.travel_from + (Decimal("0") if has_hotel_prices else it.hotel_from)
    logo = Path(settings.BASE_DIR) / "static" / "brand" / "logo-full.png"
    # every option's all-in price (package + extras + that lodge), so the client can compare in one place
    base = it.day_sell + it.lines_total + it.travel_from
    options = [{"name": x["h"].name, "meal": x["h"].meal_plan, "per_person": x["h"].price_per_person, "lodging": x["total"],
                "all_in": base + x["total"], "all_in_pp": (base + x["total"]) / it.pax if it.pax else None}
               for x in hotels if x["h"].price_per_person]
    nights_end = (it.start_date + timedelta(days=it.nights)) if it.start_date and it.nights else None
    agent = it.enquiry.assigned_agent
    glance = {"from_pp": min((o["all_in_pp"] for o in options if o["all_in_pp"]), default=None), "route": [e["day"].title for e in days][:12], "end_date": nights_end, "options": len(hotels),
              "consultant": agent.full_name if agent else "", "consultant_phone": (agent.phone if agent and agent.phone else company.phone)}
    return {"cols": cols, "options": options, "glance": glance, "it": it, "company": company, "accent": ACCENT, "accent2": ACCENT2, "logo": ((settings.STATIC_URL + "brand/logo-full.png") if web else logo.as_uri()) if logo.exists() else "", "web": web, "cover_photos": cover,
            "days": days, "rows": rows, "photo_mm": img_h, "font_pt": font_pt, "n_days": len(days) or it.nights + 1, "n_nights": it.nights,
            "has_hotel_prices": has_hotel_prices, "hotels": hotels, "prices": prices, "total": total, "travel": travel_rows(it),
            "per_person": (total / it.pax) if it.pax else None}


def travel_rows(it) -> list:
    """The ways of travelling offered, priced for the whole group, each with how much more it costs than the cheapest."""
    opts = [t for t in it.travel_options.all() if t.price]
    cheapest = min((t.total for t in opts), default=Decimal("0"))
    return [{"t": t, "total": t.total, "per_person": t.per_person, "extra": t.total - cheapest, "cheapest": t.total == cheapest} for t in opts]


def _travel_price_row(it):
    opts = [t for t in it.travel_options.all() if t.price]
    if not opts:
        return []
    c = min(opts, key=lambda t: t.total)
    return [{"description": f"Travel: {c.name or c.get_mode_display()}" + (" (cheapest option)" if len(opts) > 1 else ""), "quantity": 1,
             "unit_price": c.total, "line_total": c.total}]


def pricing_summary(it) -> dict:
    """The same money picture as the PDF, without file paths: package + extras, each lodge option all-in, and the total."""
    hotels = list(it.hotels.select_related("photo_pack", "accommodation__photo_pack"))
    base = it.day_sell + it.lines_total + it.travel_from
    options = [{"h": h, "all_in": base + h.total, "all_in_pp": (base + h.total) / it.pax if it.pax else None}
               for h in hotels if h.price_per_person]
    prices = []
    if it.day_sell:
        prices.append({"description": f"Safari package: {it.day_count} day{'s' if it.day_count != 1 else ''} (lodging, activities, vehicle and guide)",
                       "quantity": 1, "unit_price": it.day_sell, "line_total": it.day_sell})
    prices += [{"description": p.description, "quantity": p.quantity, "unit_price": p.unit_price, "line_total": p.line_total} for p in it.price_lines.all()]
    prices += _travel_price_row(it)
    total = base + (Decimal("0") if options else it.hotel_from)
    return {"travel": travel_rows(it), "hotels": hotels, "options": options, "prices": prices, "total": total, "per_person": (total / it.pax) if it.pax else None,
            "from_pp": min((o["all_in_pp"] for o in options if o["all_in_pp"]), default=None)}


def build_pdf(it, company) -> bytes:
    from weasyprint import HTML

    html = render_to_string("sales/itinerary_slides.html", build_context(it, company))
    return HTML(string=html, base_url=str(settings.BASE_DIR)).write_pdf()
