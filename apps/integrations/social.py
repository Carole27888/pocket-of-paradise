"""Mock implementations of the social / ads / review / OTA integrations.

Each service returns realistic, deterministic-per-day data so every dashboard works with no API keys. The live
classes read credentials from the environment and are the extension point for real connections (see ROADMAP).
"""
import hashlib
import random
from datetime import date, timedelta

from .base import BaseService, register


def _rng(*parts) -> random.Random:
    return random.Random(int(hashlib.md5("|".join(map(str, parts)).encode()).hexdigest()[:12], 16))


class SocialService(BaseService):
    def account_stats(self, key: str, day: date) -> dict:
        raise NotImplementedError

    def daily_ads(self, campaign_name: str, key: str, day: date) -> dict:
        raise NotImplementedError


class _MockSocial(SocialService):
    def account_stats(self, key, day):
        r = _rng(self.service_name, key, day)
        base = {"facebook": 18400, "instagram": 24600, "tiktok": 31200, "youtube": 9800, "google": 5200,
                "tripadvisor": 1900}.get(key, 4000)
        growth = (day - date(2025, 1, 1)).days * r.randint(2, 9)
        return {"followers": base + growth, "reach": r.randint(1500, 14000), "engagement_rate": round(r.uniform(1.4, 7.2), 2),
                "profile_views": r.randint(80, 900)}

    def daily_ads(self, campaign_name, key, day):
        r = _rng("ads", campaign_name, key, day)
        impressions = r.randint(4000, 38000)
        clicks = int(impressions * r.uniform(0.012, 0.035))
        return {"spend": round(r.uniform(900, 4200), 2), "impressions": impressions, "reach": int(impressions * 0.72),
                "clicks": clicks, "leads": max(0, int(clicks * r.uniform(0.02, 0.09)))}


for _name in ("meta", "tiktok", "google_ads", "youtube"):
    register(_name, "mock")(type(f"Mock_{_name}", (_MockSocial,), {}))


@register("meta", "live")
class LiveMeta(SocialService):
    """Meta Graph API: set META_ACCESS_TOKEN. Implement the two methods against /{page-id}/insights and /act_{id}/insights."""

    def account_stats(self, key, day):
        raise NotImplementedError("Set INTEGRATIONS_MODE=mock until Meta credentials are configured")

    daily_ads = account_stats


class ReviewsService(BaseService):
    def latest(self, source: str, since: date) -> list[dict]:
        raise NotImplementedError


GUESTS = ["Emma T.", "Lukas S.", "Chloe M.", "Michael J.", "Giulia R.", "Wanjiku B.", "Ahmed A.", "Sophie V.", "Daniel K.",
          "Olivia W.", "Priya S.", "Kofi M.", "Grace N.", "Peter O.", "Akinyi O."]
REVIEW_TEXT = {
    5: ["Unforgettable safari. Our guide found the cats every single day.", "Superbly organised from pickup to drop-off.",
        "The best trip we have ever taken, the team was warm and professional."],
    4: ["Great trip overall, the lodge was lovely. Pickup was a little late.", "Wonderful guide, food could be more varied."],
    3: ["Good game viewing but communication before the trip could be better."],
    2: ["Vehicle was uncomfortable on the long drives."],
}


@register("google_reviews", "mock")
@register("tripadvisor", "mock")
class MockReviews(ReviewsService):
    def latest(self, source, since):
        out, today = [], date.today()
        d = since
        while d <= today:
            r = _rng("review", source, d)
            if r.random() < 0.35:
                rating = r.choices([5, 4, 3, 2], [62, 26, 8, 4])[0]
                out.append({"external_id": f"{source}-{d:%Y%m%d}", "guest_name": r.choice(GUESTS), "rating": rating,
                            "text": r.choice(REVIEW_TEXT[rating]), "review_date": d})
            d += timedelta(days=1)
        self.log("latest", detail=f"{source}: {len(out)} reviews")
        return out


class InboxService(BaseService):
    def fetch(self, key: str, since: date) -> list[dict]:
        raise NotImplementedError


QUESTIONS = ["Hi, how much is the 3-day Mara package for 2 adults in July?", "Do you do airport pickups?",
             "Is Amboseli good in March? We are a family of 4.", "Can I pay the deposit by M-PESA?",
             "What dates are available for the Diani beach package?", "Do you have group rates for 12 people?",
             "Looks amazing! How do I book?", "Is travel insurance included?"]


@register("inbox", "mock")
class MockInbox(InboxService):
    def fetch(self, key, since):
        out, today = [], date.today()
        d = since
        while d <= today:
            r = _rng("inbox", key, d)
            for i in range(r.randint(0, 3)):
                out.append({"external_id": f"{key}-{d:%Y%m%d}-{i}", "sender": r.choice(GUESTS),
                            "message": r.choice(QUESTIONS), "kind": r.choice(["message", "message", "comment"]),
                            "received_at": d})
            d += timedelta(days=1)
        return out


class OTAService(BaseService):
    def enquiries(self, since: date) -> list[dict]:
        raise NotImplementedError


@register("ota", "mock")
class MockOTA(OTAService):
    PORTALS = ["SafariBookings", "TripAdvisor", "GetYourGuide", "Viator"]

    def enquiries(self, since):
        out, today = [], date.today()
        d = since
        while d <= today:
            r = _rng("ota", d)
            if r.random() < 0.3:
                portal = r.choice(self.PORTALS)
                out.append({"external_id": f"{portal}-{d:%Y%m%d}", "portal": portal, "name": r.choice(GUESTS),
                            "phone": f"+4477009{r.randint(10000, 99999)}", "email": f"guest{d:%d%m}@example.com",
                            "note": f"Enquiry via {portal}", "adults": r.choice([1, 2, 2, 4])})
            d += timedelta(days=1)
        return out
