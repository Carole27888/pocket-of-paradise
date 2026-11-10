from datetime import timedelta
from decimal import Decimal

import pytest
from django.db import connection
from django.utils import timezone

from apps.contacts.models import Client
from apps.core.jobs import registry
from apps.core.jobs.models import Job, JobLock, ScheduleState
from apps.core.jobs.runner import acquire_lock, enqueue, enqueue_due_schedules, execute_job, release_lock, run_due
from apps.core.money import format_money
from apps.core.models import Currency, ExchangeRate, Sequence
from apps.core.phone import display_phone, is_kenyan, normalise_phone, whatsapp_link

CALLS = []


@registry.task("tests.ok")
def _ok(x=1):
    CALLS.append(x)
    return x * 2


@registry.task("tests.boom")
def _boom():
    raise RuntimeError("boom")


# ------------------------------------------------------------------ phones
@pytest.mark.parametrize("raw,expected", [
    ("0712 345 678", "+254712345678"), ("712345678", "+254712345678"), ("254712345678", "+254712345678"),
    ("+254 712-345-678", "+254712345678"), ("0110123456", "+254110123456"), ("+44 7700 900123", "+447700900123"),
    ("0044 7700 900123", "+447700900123"), ("", ""), (None, ""),
])
def test_normalise_phone(raw, expected):
    assert normalise_phone(raw) == expected


def test_phone_helpers():
    assert is_kenyan("0712345678") and not is_kenyan("+447700900123")
    assert whatsapp_link("0712 345 678") == "https://wa.me/254712345678"
    assert display_phone("0712345678") == "+254 712 345 678"


def test_money_format():
    assert format_money(Decimal("48500"), "KES").replace("\u00a0", " ").startswith("KES 48,500")
    assert "890" in format_money(890, "USD")


# ------------------------------------------------------------------ sequences
@pytest.mark.django_db
def test_sequence_numbers_are_gap_free():
    nums = [Sequence.next_number("invoice", "INV", 4) for _ in range(3)]
    assert nums == ["INV-0001", "INV-0002", "INV-0003"]
    assert Sequence.next_number("enquiry", "ENQ", 5) == "ENQ-00001"


# ------------------------------------------------------------------ jobs
@pytest.mark.django_db
def test_enqueue_sync_runs_inline(settings):
    settings.JOBS_BACKEND = "sync"
    job = enqueue("tests.ok", x=21)
    job.refresh_from_db()
    assert job.status == Job.DONE and job.result == "42"


@pytest.mark.django_db
def test_enqueue_cron_waits_for_runner(settings):
    settings.JOBS_BACKEND = "cron"
    job = enqueue("tests.ok", x=2)
    assert Job.objects.get(pk=job.pk).status == Job.QUEUED
    res = run_due(time_limit=10)
    assert res["ran"] >= 1 and not res["locked"]
    assert Job.objects.get(pk=job.pk).status == Job.DONE


@pytest.mark.django_db
def test_unknown_task_rejected():
    with pytest.raises(KeyError):
        enqueue("tests.nope")


@pytest.mark.django_db
def test_retries_with_backoff_then_fails(settings):
    settings.JOBS_MAX_ATTEMPTS = 3
    job = Job.objects.create(task_name="tests.boom")
    before = timezone.now()
    execute_job(job)
    assert job.status == Job.QUEUED and job.attempts == 1
    assert job.run_at >= before + timedelta(minutes=2)
    execute_job(job)
    assert job.status == Job.QUEUED and job.run_at >= before + timedelta(minutes=4)
    execute_job(job)
    assert job.status == Job.FAILED and "boom" in job.last_error


@pytest.mark.django_db
def test_runner_lock_blocks_second_runner():
    owner = acquire_lock(ttl=60)
    assert owner
    assert acquire_lock(ttl=60) is None
    assert run_due(time_limit=5)["locked"] is True
    release_lock(owner)
    assert acquire_lock(ttl=60) is not None


@pytest.mark.django_db
def test_expired_lock_can_be_taken_over():
    owner = acquire_lock(ttl=60)
    JobLock.objects.filter(owner=owner).update(locked_until=timezone.now() - timedelta(seconds=1))
    assert acquire_lock(ttl=60) is not None


@pytest.mark.django_db
def test_schedules_enqueue_once_per_slot(settings):
    settings.JOBS_BACKEND = "cron"
    registry.SCHEDULES["tests.interval"] = registry.Schedule("tests.interval", "tests.ok", "interval", minutes=5)
    try:
        now = timezone.now()
        enqueue_due_schedules(now)
        assert Job.objects.filter(schedule_name="tests.interval").count() == 1
        enqueue_due_schedules(now + timedelta(minutes=1))
        assert Job.objects.filter(schedule_name="tests.interval").count() == 1
        enqueue_due_schedules(now + timedelta(minutes=6))
        assert Job.objects.filter(schedule_name="tests.interval").count() == 2
        state = ScheduleState.objects.get(name="tests.interval")
        assert state.next_run_at > now + timedelta(minutes=6)
    finally:
        registry.SCHEDULES.pop("tests.interval", None)


def test_daily_and_monthly_next_run():
    tz_now = timezone.localtime().replace(hour=8, minute=0, second=0, microsecond=0)
    daily = registry.Schedule("d", "tests.ok", "daily", hour=6, minute=0)
    nxt = timezone.localtime(daily.next_after(tz_now))
    assert nxt.hour == 6 and nxt.date() == (tz_now + timedelta(days=1)).date()
    monthly = registry.Schedule("m", "tests.ok", "monthly", day=1, hour=6)
    m = timezone.localtime(monthly.next_after(tz_now.replace(day=2)))
    assert m.day == 1 and m > tz_now.replace(day=2)


@pytest.mark.django_db
def test_run_jobs_command(settings):
    from django.core.management import call_command

    settings.JOBS_BACKEND = "cron"
    call_command("run_jobs")
    assert JobLock.objects.get(name="run_jobs").last_heartbeat is not None


@pytest.mark.django_db
def test_fx_refresh_task_stores_rates():
    for code, base in (("KES", True), ("USD", False)):
        Currency.objects.create(code=code, name=code, symbol=code, is_base=base)
    from apps.core.jobs.registry import TASKS

    TASKS["integrations.refresh_exchange_rates"]()
    assert ExchangeRate.objects.filter(currency_id="USD").exists()


# ------------------------------------------------------------------ encryption
@pytest.mark.django_db
def test_passport_encrypted_at_rest():
    c = Client.objects.create(full_name="Emma Thompson", phone="+447700900123", passport_number="123456789")
    with connection.cursor() as cur:
        cur.execute("SELECT passport_number, passport_hash FROM contacts_client WHERE id = %s", [c.pk])
        raw, h = cur.fetchone()
    assert raw.startswith("enc$") and "123456789" not in raw
    assert len(h) == 64
    assert Client.objects.get(pk=c.pk).passport_number == "123456789"
    assert c.masked_passport.endswith("789")
    assert c.is_international
