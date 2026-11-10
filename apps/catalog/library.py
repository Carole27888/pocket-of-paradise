"""Photo library: a tagged photo pack per destination or lodge, picked per day in the itinerary builder."""
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db.models import Count, Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.accounts.permissions import module_required

from .models import LibraryPhoto, PhotoCategory

KINDS = ("destination", "accommodation")


def _can_edit(user) -> bool:
    return bool(user.is_superuser or user.can_edit_operations or user.can_work_sales)


def _need_edit(user):
    if not _can_edit(user):
        raise PermissionDenied


def tag_counts(qs, limit=40):
    counts = {}
    for raw in qs.values_list("tags", flat=True):
        for t in raw.split(", "):
            if t:
                counts[t] = counts.get(t, 0) + 1
    return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:limit]


@module_required("operations", "sales")
def library(request):
    kind = request.GET.get("kind") if request.GET.get("kind") in KINDS else "destination"
    if request.method == "POST":
        _need_edit(request.user)
        name = (request.POST.get("name") or "").strip()[:100]
        k = request.POST.get("kind") if request.POST.get("kind") in KINDS else kind
        if not name or PhotoCategory.objects.filter(name__iexact=name).exists():
            messages.error(request, "Give the pack a name that isn't already used.")
            return redirect(f"{request.path}?kind={k}")
        cat = PhotoCategory.objects.create(name=name, kind=k, description=(request.POST.get("description") or "")[:255])
        messages.success(request, f"Pack “{cat.name}” created. Add some photos.")
        return redirect("catalog:photo_pack", slug=cat.slug)
    cats = PhotoCategory.objects.filter(kind=kind).annotate(n=Count("images")).prefetch_related("images")
    counts = {k: PhotoCategory.objects.filter(kind=k).count() for k in KINDS}
    return render(request, "catalog/library.html", {"categories": cats, "kind": kind, "counts": counts, "can_edit": _can_edit(request.user)})


@module_required("operations", "sales")
def photo_pack(request, slug):
    cat = get_object_or_404(PhotoCategory, slug=slug)
    if request.method == "POST":
        _need_edit(request.user)
        from PIL import Image

        added = skipped = 0
        tags = LibraryPhoto.normalize_tags(request.POST.get("tags", ""))
        for f in request.FILES.getlist("images"):
            try:
                Image.open(f).verify()
                f.seek(0)
            except Exception:
                skipped += 1
                continue
            LibraryPhoto.objects.create(category=cat, image=f, tags=tags)
            added += 1
        if request.headers.get("x-requested-with") == "XMLHttpRequest":
            return JsonResponse({"added": added, "skipped": skipped})
        if added:
            messages.success(request, f"Added {added} photo{'s' if added != 1 else ''}.")
        if skipped:
            messages.error(request, f"{skipped} file{'s' if skipped != 1 else ''} skipped (not valid images).")
        return redirect("catalog:photo_pack", slug=slug)
    q = request.GET.get("q", "").strip()
    tag = request.GET.get("tag", "").strip().lower()
    images = cat.images.all()
    for term in q.split():
        images = images.filter(Q(tags__icontains=term) | Q(caption__icontains=term))
    if tag == "__untagged":
        images = images.filter(tags="")
    elif tag:
        images = [i for i in images if tag in i.tag_list]
    return render(request, "catalog/photo_pack.html", {
        "category": cat, "images": images, "q": q, "tag": tag, "tag_counts": tag_counts(cat.images.all()),
        "untagged": cat.images.filter(tags="").count(), "tagged": cat.images.exclude(tags="").count(), "total": cat.images.count(), "can_edit": _can_edit(request.user)})


@module_required("operations", "sales")
def photo_search(request):
    """JSON feed for the 'Choose photos' picker in the itinerary builder."""
    q = request.GET.get("q", "").strip()
    cat = request.GET.get("category", "")
    tag = request.GET.get("tag", "").strip().lower()
    scope = LibraryPhoto.objects.select_related("category")
    if cat.isdigit():
        scope = scope.filter(category_id=cat)
    qs = scope
    for term in q.split():
        qs = qs.filter(Q(tags__icontains=term) | Q(caption__icontains=term) | Q(category__name__icontains=term))
    if tag == "__untagged":
        qs = qs.filter(tags="")
    matches = [i for i in qs if tag in i.tag_list] if tag and tag != "__untagged" else list(qs)
    return JsonResponse({"total": len(matches), "tags": tag_counts(scope), "untagged": scope.filter(tags="").count(),
                         "results": [{"id": i.pk, "thumb": i.thumb_url, "tags": i.tags, "category": i.category.name} for i in matches[:150]]})


@require_POST
@module_required("operations", "sales")
def photos_bulk(request, slug):
    _need_edit(request.user)
    cat = get_object_or_404(PhotoCategory, slug=slug)
    sel = cat.images.filter(pk__in=request.POST.getlist("selected"))
    action = request.POST.get("action")
    if action == "delete":
        n = sel.count()
        for im in sel:   # per object so the files leave the disk too
            im.delete()
        messages.success(request, f"Deleted {n} photo{'s' if n != 1 else ''}.")
    else:
        tags = LibraryPhoto.normalize_tags(request.POST.get("tags", ""))
        mode = request.POST.get("mode", "add")
        if not tags and mode != "set":
            messages.error(request, "Type at least one tag first.")
            return redirect("catalog:photo_pack", slug=slug)
        n = 0
        for im in sel:
            im.tags = tags if mode == "set" else ", ".join(im.tag_list + tags.split(", "))
            im.save(update_fields=["tags"])
            n += 1
        messages.success(request, f"Updated tags on {n} photo{'s' if n != 1 else ''}.")
    return redirect("catalog:photo_pack", slug=slug)


@require_POST
@module_required("operations", "sales")
def pack_delete(request, slug):
    _need_edit(request.user)
    cat = get_object_or_404(PhotoCategory, slug=slug)
    for im in cat.images.all():
        im.delete()
    cat.delete()
    messages.success(request, "Pack deleted.")
    return redirect("catalog:library")
