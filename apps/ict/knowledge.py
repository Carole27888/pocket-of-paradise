"""What the bot has been taught. Retrieval is plain word overlap, so it works with no AI key and the answer shown is always one the team wrote."""
import re
from collections import Counter

from django.db.models import F

from .models import BotMessage, KnowledgeEntry

STOP = set("""the and for are you your with that this have has had can could would will what when where which who how why not but from they them their our
about any some there here then than also just into over only very much many does did was were been being get got let please kindly hello hi hey thanks thank
dear sir madam mam""".split())
SW_STOP = set("na ya wa kwa la ni za ili hii hiyo ile yake yako yangu je tafadhali asante".split())
WORD = re.compile(r"[a-z0-9]+")


def tokens(text: str) -> set:
    return {w for w in WORD.findall((text or "").lower()) if len(w) > 2 and w not in STOP and w not in SW_STOP}


def _stem(w: str) -> str:
    for suf in ("ing", "es", "s", "ed"):
        if w.endswith(suf) and len(w) - len(suf) >= 3:
            return w[: -len(suf)]
    return w


def _bag(text: str) -> set:
    return {_stem(w) for w in tokens(text)}


def score(query_bag: set, entry: KnowledgeEntry) -> float:
    eb = _bag(f"{entry.question} {entry.keywords} {entry.keywords}")
    if not eb or not query_bag:
        return 0.0
    hit = len(eb & query_bag)
    return 0.65 * hit / len(eb) + 0.35 * hit / len(query_bag)


def search(text: str, limit=3, min_score=0.2) -> list:
    qb = _bag(text)
    ranked = sorted(((score(qb, e), e) for e in KnowledgeEntry.objects.filter(is_active=True)), key=lambda t: -t[0])
    return [(s, e) for s, e in ranked[:limit] if s >= min_score]


def best_answer(text: str, min_score: float):
    """The one entry that clearly answers this message, or None."""
    found = search(text, 1, min_score)
    if not found:
        return None
    entry = found[0][1]
    KnowledgeEntry.objects.filter(pk=entry.pk).update(times_used=F("times_used") + 1)
    return entry


def prompt_block(text: str) -> str:
    """The most relevant taught answers, for the AI to rely on in preference to its own guesses."""
    hits = search(text, 4, 0.15)
    if not hits:
        return ""
    return "TEAM-APPROVED ANSWERS (use these facts exactly; they override anything else):\n" + "\n".join(f"- Q: {e.question}\n  A: {e.answer}" for _, e in hits)


def unanswered(days=45, limit=12) -> list:
    """Questions customers ask that nothing taught covers yet, most repeated first: the next things worth teaching the bot."""
    from datetime import timedelta

    from django.utils import timezone

    since = timezone.now() - timedelta(days=days)
    seen = Counter()
    sample = {}
    for body in BotMessage.objects.filter(sender=BotMessage.CLIENT, created_at__gte=since).values_list("body", flat=True)[:3000]:
        b = _bag(body)
        if len(b) < 2 or len(body) > 220:
            continue
        if not ("?" in body or body.lower().split()[0] in ("what", "how", "when", "where", "can", "do", "is", "are", "which", "why", "naomba", "je")):
            continue
        if search(body, 1, 0.45):
            continue
        key = " ".join(sorted(b))
        seen[key] += 1
        sample.setdefault(key, body.strip())
    return [{"question": sample[k], "count": n} for k, n in seen.most_common(limit)]


def seed_from_catalogue(user=None) -> int:
    """Teach the bot the basics straight from the package catalogue: what each trip includes and excludes, and its length and price."""
    from apps.catalog.models import TourPackage

    made = 0
    for p in TourPackage.objects.alive().filter(status__in=["published", "seasonal"]):
        rows = [(f"How many days is the {p.title}?", f"The {p.title} is {p.days} days and {p.nights} nights. A consultant will confirm dates and the exact quotation.", f"{p.title}, duration, length"),
                (f"How much is the {p.title}?", f"The {p.title} starts from {p.currency} {p.price_per_person:,.0f} per person. The exact price depends on dates, group size and lodges, and a consultant will send a quotation.", f"{p.title}, price, cost, rates")]
        if p.inclusions.strip():
            rows.append((f"What is included in the {p.title}?", "Included: " + "; ".join(l.strip() for l in p.inclusions.splitlines() if l.strip())[:700], f"{p.title}, included, inclusions, covers"))
        if p.exclusions.strip():
            rows.append((f"What is not included in the {p.title}?", "Not included: " + "; ".join(l.strip() for l in p.exclusions.splitlines() if l.strip())[:700], f"{p.title}, excluded, exclusions, extra"))
        for q, a, kw in rows:
            _, created = KnowledgeEntry.objects.get_or_create(question=q[:240], defaults={"answer": a[:900], "keywords": kw[:240], "category": p.title[:40], "source": KnowledgeEntry.CATALOG, "created_by": user})
            made += created
    return made
