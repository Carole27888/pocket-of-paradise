"""Writes the words for an itinerary day or intro. With an AI key it asks the model, with only the facts the agent entered; with none it composes
a clean paragraph from the catalogue facts, so the button always gives the agent something to edit."""
from apps.ict import ai

SYSTEM = """You write copy for safari itineraries sent to clients of Pocket of Paradise Travel Company, a Kenyan safari company.
Write 2 to 3 warm, vivid sentences in second person ("you"). Use ONLY the facts given. Never invent animals sightings guaranteed, prices, times, or places not listed.
Plain text, no headings, no bullet points, no emoji."""


def _join(items):
    items = [i for i in items if i]
    return ", ".join(items[:-1]) + (" and " if len(items) > 1 else "") + items[-1] if items else ""


def facts_for_day(f: dict) -> dict:
    from apps.catalog.models import Accommodation, Destination

    dest = Destination.objects.filter(pk=f.get("destination") or 0).first()
    acc = Accommodation.objects.filter(pk=f.get("accommodation") or 0).first()
    acts = [a.strip() for a in (f.get("activities") or "").split(",") if a.strip()] or (dest.activity_list[:3] if dest else [])
    meals = [n for n, k in (("breakfast", "breakfast"), ("lunch", "lunch"), ("dinner", "dinner")) if str(f.get(k, "")).lower() in ("1", "true", "on")]
    return {"title": (f.get("title") or "").strip(), "destination": dest.name if dest else "", "about": (dest.summary or dest.description or "")[:240] if dest else "",
            "lodge": acc.name if acc else "", "room": (f.get("room_type") or "").strip(), "activities": acts, "meals": meals,
            "drive_hours": (f.get("drive_hours") or "").strip(), "drive_km": (f.get("drive_km") or "").strip()}


def compose_day(x: dict) -> str:
    parts = []
    first = x["title"] or (f"A day in {x['destination']}" if x["destination"] else "Your day")
    if x["drive_hours"] or x["drive_km"]:
        dist = f"{x['drive_hours']} hours" if x["drive_hours"] else f"{x['drive_km']} km"
        parts.append(f"{first}: settle in for a scenic drive of about {dist}" + (f" to {x['destination']}." if x["destination"] else "."))
    elif x["destination"]:
        parts.append(f"{first}: today is all about {x['destination']}.")
    else:
        parts.append(f"{first}.")
    if x["about"] and x["destination"]:
        parts.append(x["about"].rstrip(".") + ".")
    if x["activities"]:
        parts.append(f"You will enjoy {_join(x['activities']).lower()}.")
    tail = []
    if x["lodge"]:
        tail.append(f"Tonight you stay at {x['lodge']}" + (f" in a {x['room'].lower()}" if x["room"] else ""))
    if x["meals"]:
        tail.append(f"{'meals included: ' if tail else 'Meals included: '}{_join(x['meals'])}")
    if tail:
        parts.append(", ".join(tail) + ".")
    return " ".join(parts)


def write_day(fields: dict) -> dict:
    x = facts_for_day(fields)
    if ai.configured():
        brief = "\n".join(f"{k}: {v}" for k, v in x.items() if v)
        try:
            text = ai.text_of(ai.complete(SYSTEM, [{"role": "user", "content": f"Write the description for this itinerary day.\n{brief}"}], max_tokens=220, feature="writer"))
            if text:
                return {"text": text, "mode": "ai"}
        except ai.AIUnavailable:
            pass
    return {"text": compose_day(x), "mode": "template"}


def write_intro(it) -> dict:
    first = it.enquiry.client.full_name.split()[0]
    places = []
    for d in it.days.select_related("destination"):
        if d.destination_id and d.destination.name not in places:
            places.append(d.destination.name)
    base = (f"Dear {first}, thank you for choosing Pocket of Paradise Travel Company. Here is the {it.day_count}-day journey we have planned for {it.enquiry.party_label}"
            f"{' through ' + _join(places) if places else ''}. Look it over, tell us what you would like to change, and we will adjust it until it is exactly right.")
    if ai.configured():
        try:
            text = ai.text_of(ai.complete(SYSTEM, [{"role": "user", "content": f"Write a short welcome paragraph for the top of this itinerary for {first}. Facts: {it.day_count} days, party {it.enquiry.party_label}, places: {_join(places) or 'not set'}. Max 70 words."}], max_tokens=200, feature="writer"))
            if text:
                return {"text": text, "mode": "ai"}
        except ai.AIUnavailable:
            pass
    return {"text": base, "mode": "template"}
