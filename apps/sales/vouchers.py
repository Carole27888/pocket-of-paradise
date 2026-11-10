"""Accommodation vouchers: one page per lodge stay, built from the itinerary, to hand to the client or send to the lodge."""
from datetime import timedelta


def meal_plan(days) -> str:
    b, l, d = (any(getattr(x, k) for x in days) for k in ("breakfast", "lunch", "dinner"))
    if b and l and d:
        return "Full Board"
    if b and d:
        return "Half Board"
    if b and not l and not d:
        return "Bed & Breakfast"
    return ", ".join(n for n, f in (("Breakfast", b), ("Lunch", l), ("Dinner", d)) if f) or "Room only"


def stays(it) -> list:
    """Consecutive nights at the same lodge become one stay."""
    out, cur = [], None
    for d in it.days.select_related("accommodation__destination"):
        if not d.accommodation_id:
            cur = None
            continue
        if cur and cur["acc"].pk == d.accommodation_id and cur["last"] + 1 == d.day_number:
            cur["days"].append(d)
            cur["last"] = d.day_number
            continue
        cur = {"acc": d.accommodation, "days": [d], "last": d.day_number, "first": d.day_number}
        out.append(cur)
    result = []
    for s in out:
        first = s["days"][0]
        start = it.start_date + timedelta(days=first.day_number - 1) if it.start_date else None
        n = len(s["days"])
        result.append({"lodge": s["acc"], "destination": s["acc"].destination, "nights": n, "check_in": start, "check_out": start + timedelta(days=n) if start else None,
                       "room": next((d.room_type for d in s["days"] if d.room_type), ""), "meals": meal_plan(s["days"])})
    return result


def request_text(it, stay, company) -> str:
    """A short booking request to send to a lodge: dates, nights, guests, room and meal plan."""
    when = f"{stay['check_in']:%a %d %b %Y} to {stay['check_out']:%a %d %b %Y}" if stay["check_in"] else "dates to be confirmed"
    first = (stay["lodge"].contact_name or "").split()[0] if stay["lodge"].contact_name else "team"
    return (f"Hello {first}, this is {company.name}. Please confirm availability and the rate for {stay['lodge'].name}:\n"
            f"- Guests: {it.enquiry.party_label} ({it.pax} guest{'s' if it.pax != 1 else ''})\n- Dates: {when} ({stay['nights']} night{'s' if stay['nights'] != 1 else ''})\n"
            f"{('- Room: ' + stay['room'] + chr(10)) if stay['room'] else ''}- Meal plan: {stay['meals']}\n- Reference: {it.enquiry.reference}\n"
            f"Kindly reply with your confirmation. Thank you.")


def ics(booking, it, company) -> str:
    """The trip as a calendar file: one all-day event for the whole trip, plus one per day when there is an itinerary."""
    from datetime import timedelta

    def d(x):
        return x.strftime("%Y%m%d")

    def esc(t):
        return (t or "").replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")

    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", f"PRODID:-//{esc(company.name)}//Trip//EN", "CALSCALE:GREGORIAN"]
    if booking.travel_start:
        end = (booking.travel_end or booking.travel_start) + timedelta(days=1)
        lines += ["BEGIN:VEVENT", f"UID:{booking.reference}@trip", f"DTSTART;VALUE=DATE:{d(booking.travel_start)}", f"DTEND;VALUE=DATE:{d(end)}",
                  f"SUMMARY:{esc(booking.title)}", f"DESCRIPTION:{esc('Booking ' + booking.reference + ' with ' + company.name)}", "END:VEVENT"]
        for day in (it.days.all() if it else []):
            when = booking.travel_start + timedelta(days=day.day_number - 1)
            lines += ["BEGIN:VEVENT", f"UID:{booking.reference}-day{day.day_number}@trip", f"DTSTART;VALUE=DATE:{d(when)}", f"DTEND;VALUE=DATE:{d(when + timedelta(days=1))}",
                      f"SUMMARY:{esc(f'Day {day.day_number}: {day.title}')}", f"DESCRIPTION:{esc(day.description[:400])}", "END:VEVENT"]
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n"
