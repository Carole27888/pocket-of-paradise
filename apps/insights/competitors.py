"""Competitor watcher.

- Websites: each page listed for a competitor is fetched (publicly, like a browser would), reduced to its readable text and compared with
  the last check. New and removed lines are recorded, with any prices seen, and a change alerts the people who watch competitors.
- Google profiles: rating, number of reviews and the newest reviews come from the official Google Places API (key in ICT → API Keys).
- Social pages are not read: Facebook, Instagram, TikTok and YouTube block automated reading and forbid it in their terms. Their links are
  kept on the competitor so the team can open them in one click.
- "Search the web": the AI runs a live web search (apps.ict.ai.web_search) and answers with its sources.
"""
import difflib
import hashlib
import json
import logging
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import timedelta
from html.parser import HTMLParser

from django.db.models import Count
from django.utils import timezone

from apps.accounts import roles as R
from apps.system.services import get_credential

from .models import Competitor, MarketSearch, Snapshot

log = logging.getLogger(__name__)
WATCHERS = (R.ADMIN, R.DIRECTOR, R.COO, R.OPERATIONS, R.SALES_MANAGER)
UA = "Mozilla/5.0 (compatible; DaysSafarisCompetitorWatch/1.0; +https://dayssafaris.co.ke)"
MAX_BYTES = 2_000_000
PRICE_RE = re.compile(r"(?:(?:USD|US\$|KES|KSH|Ksh|EUR|GBP|\$|€|£)\s?\d[\d,]*(?:\.\d+)?(?:\s?(?:pp|per person|p\.p\.))?"
                      r"|\d[\d,]*(?:\.\d+)?\s?(?:USD|KES|KSH|Ksh|EUR|GBP)(?:\s?(?:pp|per person))?)")
PLACES = "https://places.googleapis.com/v1"


def can_watch(user) -> bool:
    return bool(user.is_superuser or user.has_role(*WATCHERS))


# ------------------------------------------------------------------ websites
class _Text(HTMLParser):
    """The text a visitor reads: no scripts, styles, menus' hidden bits or markup."""

    SKIP = {"script", "style", "noscript", "svg", "template", "head"}
    BLOCK = {"p", "div", "li", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "br", "section", "article", "td", "th", "header", "footer", "a", "span"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.lines, self.cur, self.skip, self.title, self._in_title = [], [], 0, "", False

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self.skip += 1
        if tag == "title":
            self._in_title = True
        if tag in self.BLOCK:
            self._flush()

    def handle_endtag(self, tag):
        if tag in self.SKIP and self.skip:
            self.skip -= 1
        if tag == "title":
            self._in_title = False
        if tag in self.BLOCK:
            self._flush()

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        elif not self.skip:
            self.cur.append(data)

    def _flush(self):
        line = " ".join("".join(self.cur).split())
        if line:
            self.lines.append(line)
        self.cur = []


def fetch(url: str) -> tuple[int, str]:
    """(status, html). The only network code for websites, so tests replace it."""
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "text/html,application/xhtml+xml", "Accept-Language": "en"})
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            raw = r.read(MAX_BYTES)
            charset = r.headers.get_content_charset() or "utf-8"
            return r.status, raw.decode(charset, "ignore")
    except urllib.error.HTTPError as e:
        return e.code, ""
    except (urllib.error.URLError, TimeoutError, ValueError) as e:
        return 0, str(getattr(e, "reason", e))


def page_text(html: str) -> tuple[str, list[str]]:
    p = _Text()
    try:
        p.feed(html)
        p.close()
    except Exception:  # broken markup: keep what was read
        pass
    p._flush()
    seen, lines = set(), []
    for l in p.lines:                                         # menus and footers repeat: keep each line once
        if len(l) > 2 and l not in seen:
            seen.add(l)
            lines.append(l[:400])
    return " ".join(p.title.split())[:300], lines


def check_page(c: Competitor, url: str) -> Snapshot:
    prev = c.snapshots.filter(source=Snapshot.WEBSITE, url=url, ok=True).first()
    status, html = fetch(url)
    if status != 200 or not html:
        return Snapshot.objects.create(competitor=c, source=Snapshot.WEBSITE, url=url, ok=False,
                                       error=(f"The site answered {status}" if status else f"Could not reach it: {html}")[:300])
    title, lines = page_text(html)
    text = "\n".join(lines)[:60000]
    digest = hashlib.sha256(text.encode()).hexdigest()
    prices = list(dict.fromkeys(m.group(0).strip() for m in PRICE_RE.finditer(text)))[:40]
    snap = Snapshot(competitor=c, source=Snapshot.WEBSITE, url=url, title=title, content_hash=digest, text=text, prices=prices)
    if prev and prev.content_hash != digest:
        old = prev.text.splitlines()
        diff = list(difflib.ndiff(old, lines))
        snap.added = [d[2:] for d in diff if d.startswith("+ ")][:60]
        snap.removed = [d[2:] for d in diff if d.startswith("- ")][:60]
        snap.changed = bool(snap.added or snap.removed)
    snap.save()
    return snap


# ------------------------------------------------------------------ Google profile (Places API)
def places_key() -> str:
    return get_credential("google_places", "api_key")


def _places(method: str, path: str, fields: str, body: dict | None = None) -> tuple[int, dict]:
    """One Places API (New) call. The only network code for Google, so tests replace it."""
    req = urllib.request.Request(f"{PLACES}/{path}", data=json.dumps(body).encode() if body is not None else None, method=method,
                                 headers={"X-Goog-Api-Key": places_key(), "X-Goog-FieldMask": fields, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"{}")
        except ValueError:
            return e.code, {}
    except (urllib.error.URLError, TimeoutError) as e:
        return 0, {"error": {"message": str(getattr(e, "reason", e))}}


def _query_from(text: str, fallback: str) -> str:
    """A Google Maps link becomes the name in it; plain text is used as typed."""
    text = (text or "").strip()
    m = re.search(r"/maps/place/([^/@?]+)", text)
    if m:
        return urllib.parse.unquote_plus(m.group(1))
    return fallback if text.startswith("http") else (text or fallback)


def check_google(c: Competitor) -> Snapshot | None:
    if not places_key() or not (c.google_query or c.name):
        return None
    if not c.google_place_id:
        status, data = _places("POST", "places:searchText", "places.id,places.displayName", {"textQuery": _query_from(c.google_query, c.name)})
        found = (data.get("places") or [{}])[0].get("id") if status == 200 else None
        if not found:
            msg = (data.get("error") or {}).get("message") or "No Google profile found for that name. Paste their Google Maps link instead."
            return Snapshot.objects.create(competitor=c, source=Snapshot.GOOGLE, ok=False, error=msg[:300])
        c.google_place_id = found
        c.save(update_fields=["google_place_id"])
    prev = c.latest_google()
    status, data = _places("GET", f"places/{c.google_place_id}", "id,displayName,rating,userRatingCount,reviews,googleMapsUri")
    if status != 200:
        return Snapshot.objects.create(competitor=c, source=Snapshot.GOOGLE, ok=False, error=((data.get("error") or {}).get("message") or f"Google answered {status}")[:300])
    since = prev.checked_at if prev else None
    new = []
    for r in data.get("reviews") or []:
        t = r.get("publishTime", "")
        try:
            when = timezone.datetime.fromisoformat(t.replace("Z", "+00:00"))
        except ValueError:
            continue
        if since is None or when > since:
            new.append({"rating": r.get("rating"), "text": ((r.get("text") or {}).get("text") or "")[:600], "author": (r.get("authorAttribution") or {}).get("displayName", ""),
                        "when": t[:10]})
    rating, count = data.get("rating"), data.get("userRatingCount")
    changed = bool(prev and (prev.rating is not None and rating is not None and float(prev.rating) != float(rating) or prev.review_count != count))
    return Snapshot.objects.create(competitor=c, source=Snapshot.GOOGLE, url=data.get("googleMapsUri", "")[:500], title=(data.get("displayName") or {}).get("text", "")[:300],
                                   rating=rating, review_count=count, new_reviews=new if prev else [], changed=changed)


# ------------------------------------------------------------------ running checks
def check(c: Competitor, notify: bool = True) -> list[Snapshot]:
    snaps = [check_page(c, u) for u in c.page_list]
    g = check_google(c)
    if g:
        snaps.append(g)
    changed = [s for s in snaps if s.ok and (s.changed or s.new_reviews)]
    if notify and changed:
        from apps.notifications.services import notify_roles

        lines = [f"{'Website' if s.source == Snapshot.WEBSITE else 'Google'}: {s.summary}" for s in changed]
        notify_roles(WATCHERS, f"{c.name} made changes", " · ".join(lines)[:280], f"/insights/competitors/{c.pk}/", "info",
                     dedupe_key=f"comp:{c.pk}:{changed[0].pk}")
    return snaps


def check_all() -> int:
    n = 0
    for c in Competitor.objects.filter(is_active=True):
        try:
            n += sum(1 for s in check(c) if s.changed or s.new_reviews)
        except Exception:  # one bad site must not stop the others
            log.exception("competitor check failed for %s", c.pk)
    return n


# ------------------------------------------------------------------ numbers for the page
def overview(days: int = 30) -> list[dict]:
    since = timezone.now() - timedelta(days=days)
    changes = dict(Snapshot.objects.filter(checked_at__gte=since, changed=True).values_list("competitor").annotate(n=Count("pk")))
    rows = []
    for c in Competitor.objects.all():
        g = c.latest_google()
        last_change = c.snapshots.filter(changed=True).first()
        last = c.snapshots.first()
        new_reviews = sum(len(s.new_reviews) for s in c.snapshots.filter(source=Snapshot.GOOGLE, checked_at__gte=since))
        rows.append({"c": c, "rating": g.rating if g else None, "reviews": g.review_count if g else None, "changes": changes.get(c.pk, 0),
                     "new_reviews": new_reviews, "last_change": last_change, "last_check": last, "failing": bool(last and not last.ok)})
    return rows


def charts(c: Competitor, weeks: int = 12) -> dict:
    """Weekly website changes and the Google rating / review count over time, for the main competitor's charts."""
    today = timezone.localdate()
    start = today - timedelta(days=today.weekday() + 7 * (weeks - 1))
    labels, counts = [], []
    for i in range(weeks):
        wk = start + timedelta(days=7 * i)
        labels.append(f"{wk:%d %b}")
        counts.append(c.snapshots.filter(changed=True, source=Snapshot.WEBSITE, checked_at__date__gte=wk, checked_at__date__lt=wk + timedelta(days=7)).count())
    g = list(c.snapshots.filter(source=Snapshot.GOOGLE, ok=True, checked_at__date__gte=start).order_by("checked_at"))
    by_day = {}
    for s in g:
        by_day[f"{timezone.localtime(s.checked_at):%d %b}"] = s
    return {"changes": {"labels": labels, "values": counts, "label": "Website changes"},
            "reviews": {"labels": list(by_day), "series": [{"label": "Google reviews", "data": [s.review_count or 0 for s in by_day.values()]}]},
            "rating": {"labels": list(by_day), "series": [{"label": "Google rating", "data": [float(s.rating) if s.rating is not None else None for s in by_day.values()]}]}}


SEARCH_SYSTEM = """You research the Kenyan and East African safari market for {company}, a safari company. Search the web and answer the question
with current, specific facts: operator or package names, what is included, length, price (with currency and whether per person), season, and
where you saw it. Prefer recent pages. Be concise and use short bullet points. Say clearly when prices or details could not be confirmed."""


def search_web(user, query: str) -> MarketSearch:
    from apps.core.models import CompanySetting
    from apps.ict import ai

    q = query.strip()[:300]
    try:
        out = ai.web_search(SEARCH_SYSTEM.format(company=CompanySetting.load().name or "Pocket of Paradise"), q)
        return MarketSearch.objects.create(query=q, answer=out["text"][:20000], sources=out["sources"], provider=out["provider"], created_by=user)
    except ai.AIUnavailable as e:
        return MarketSearch.objects.create(query=q, answer=f"The web search could not run: {e}. Check the AI key under ICT → AI.", ok=False, created_by=user)
