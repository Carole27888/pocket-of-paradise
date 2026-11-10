"""Public catalogue site: browse published packages, no login. Enquiries go through the same `ingest_lead` as every other channel."""
from django import forms
from django.core.cache import cache
from django.db.models import Q
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from apps.core.models import CompanySetting

from .models import Destination, TourPackage


def _published():
    return TourPackage.objects.alive().filter(status__in=["published", "seasonal"]).prefetch_related("destinations")


class PublicEnquiryForm(forms.Form):
    name = forms.CharField(max_length=120)
    phone = forms.CharField(max_length=24)
    email = forms.EmailField(required=False)
    adults = forms.IntegerField(min_value=1, max_value=60, initial=2)
    travel_date = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}))
    message = forms.CharField(max_length=1000, required=False, widget=forms.Textarea(attrs={"rows": 3}))
    website = forms.CharField(required=False)         # honeypot

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        for n, f in self.fields.items():
            f.widget.attrs.setdefault("class", "input")
        self.fields["website"].widget = forms.HiddenInput()


def home(request):
    qs = _published()
    q = (request.GET.get("q") or "").strip()[:60]
    dest = request.GET.get("destination") or ""
    days = request.GET.get("days") or ""
    if q:
        qs = qs.filter(Q(title__icontains=q) | Q(summary__icontains=q) | Q(destinations__name__icontains=q)).distinct()
    if dest:
        qs = qs.filter(destinations__slug=dest)
    if days.isdigit():
        qs = qs.filter(days__lte=int(days))
    return render(request, "catalog/public_home.html", {"packages": qs.order_by("price_per_person")[:60], "q": q, "dest": dest, "days": days,
                                                        "destinations": Destination.objects.alive().filter(packages__status__in=["published", "seasonal"]).distinct(),
                                                        "company": CompanySetting.load()})


def detail(request, slug):
    pkg = get_object_or_404(_published(), slug=slug)
    form = PublicEnquiryForm(request.POST or None)
    done = False
    if request.method == "POST":
        ip = request.META.get("HTTP_X_FORWARDED_FOR", request.META.get("REMOTE_ADDR", "")).split(",")[0].strip()
        key = f"rl:explore:{ip}"
        n = cache.get(key, 0) + 1
        cache.set(key, n, 3600)
        if n > 10:
            return HttpResponse("Too many submissions from this device. Please try again later.", status=429)
        if form.is_valid():
            d = form.cleaned_data
            if not d["website"]:
                from apps.marketing.services import ingest_lead

                ingest_lead(name=d["name"], phone=d["phone"], email=d["email"], source="website", message=d["message"], package=pkg, adults=d["adults"],
                            travel_date=d["travel_date"], captured_via="catalogue")
            done = True
            form = PublicEnquiryForm()
    days = pkg.itinerary.days.all() if pkg.itinerary_id else []
    return render(request, "catalog/public_detail.html", {"pkg": pkg, "form": form, "done": done, "days": days, "company": CompanySetting.load(),
                                                          "inclusions": [l for l in pkg.inclusions.splitlines() if l.strip()],
                                                          "exclusions": [l for l in pkg.exclusions.splitlines() if l.strip()]})


def sitemap(request):
    base = f"{request.scheme}://{request.get_host()}"
    urls = [base + reverse("public_catalogue")] + [base + reverse("public_package", args=[p.slug]) for p in _published()]
    body = '<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">' + "".join(f"<url><loc>{u}</loc></url>" for u in urls) + "</urlset>"
    return HttpResponse(body, content_type="application/xml")


def robots(request):
    base = f"{request.scheme}://{request.get_host()}"
    return HttpResponse(f"User-agent: *\nAllow: /explore/\nDisallow: /\nSitemap: {base}/sitemap.xml\n", content_type="text/plain")
