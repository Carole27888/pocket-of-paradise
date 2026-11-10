from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import Http404
from django.shortcuts import render
from django.utils import timezone

from apps.core.models import CompanySetting
from apps.core.pdf import render_pdf

from django.conf import settings

from . import content


def _sections(m):
    base = settings.BASE_DIR / "static" / "img" / "manual" / m.slug
    out = []
    for i, sec in enumerate(m.all_sections(), 1):
        sec.number = i
        sec.has_shot = bool(sec.shot) and (base / f"{sec.shot}.png").exists()
        out.append(sec)
    return out


@login_required
def index(request):
    return render(request, "manuals/index.html", {"manuals": content.visible(request.user)})


@login_required
def manual(request, slug):
    m = content.BY_SLUG.get(slug)
    if m is None:
        raise Http404
    if m not in content.visible(request.user):
        raise PermissionDenied
    secs = _sections(m)
    ctx = {"m": m, "sections": secs, "shots": sum(1 for x in secs if x.has_shot), "role": request.user.primary_role, "manuals": content.visible(request.user),
           "company": CompanySetting.load(), "generated": timezone.now()}
    if request.GET.get("format") == "pdf":
        return render_pdf(request, "manuals/pdf.html", ctx, f"{slug}.pdf", fallback_url=request.path)
    return render(request, "manuals/detail.html", ctx)
