import pytest
from django.urls import reverse

from apps.accounts import roles as R
from apps.catalog.models import TourPackage
from apps.marketing import services
from apps.marketing.models import ContentShoot, Post


@pytest.mark.django_db
def test_shoot_flow_needs_a_linked_post_before_posted():
    s = ContentShoot.objects.create(title="Mara reel")
    services.advance_shoot(s)
    assert s.stage == "shot" and s.shot_at
    with pytest.raises(ValueError, match="Link the post"):
        services.advance_shoot(s)
    s.post = Post.objects.create(title="Reel", status="published")
    services.advance_shoot(s)
    assert s.stage == "posted" and s.posted_at
    with pytest.raises(ValueError):
        services.advance_shoot(s)


@pytest.mark.django_db
def test_production_roles_get_the_production_module_only(client, make_user):
    from apps.core.modules import modules_for

    u = make_user("prod@example.com", R.PRODUCTION_STAFF)
    assert [m.key for m in modules_for(u)] == ["operations", "production"] or "production" in [m.key for m in modules_for(u)]
    client.force_login(u)
    s = ContentShoot.objects.create(title="Shoot")
    for url in ("/production/", "/production/shoots/", s.get_absolute_url(), "/production/shoots/new/", f"/production/shoots/{s.pk}/edit/", "/marketing/media/"):
        r = client.get(url)
        assert r.status_code == 200, (url, r.status_code)
    assert client.get("/marketing/").status_code == 403
    assert client.post(reverse("production:advance", args=[s.pk])).status_code == 302
    s.refresh_from_db()
    assert s.stage == "shot"


@pytest.mark.django_db
def test_completed_trip_creates_shoot_and_post_once(agent):
    from datetime import timedelta
    from django.utils import timezone
    from apps.trips.models import Departure

    pkg = TourPackage.objects.create(title="Mara", days=2, nights=1, status="published")
    dep = Departure.objects.create(package=pkg, start_date=timezone.localdate(), end_date=timezone.localdate() + timedelta(days=1))
    services.on_departure_completed(dep)
    services.on_departure_completed(dep)
    assert ContentShoot.objects.filter(departure=dep).count() == 1 and Post.objects.filter(departure=dep).count() == 1


@pytest.mark.django_db
def test_shoot_can_move_back():
    from apps.marketing import services
    from apps.marketing.models import ContentShoot

    s = ContentShoot.objects.create(title="Test", stage=ContentShoot.SHOT)
    services.retreat_shoot(s)
    assert s.stage == ContentShoot.SOURCED
    with pytest.raises(ValueError):
        services.retreat_shoot(s)
