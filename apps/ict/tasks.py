from apps.core.jobs import every, task


@every(1)
@task("ict.process_broadcasts")
def process_broadcasts():
    """Send the next batch of every running or due broadcast. SMS goes in batches of 100, e-mail 150 a minute."""
    from .bulk import due_broadcasts, process

    n = 0
    for b in due_broadcasts():
        c = process(b)
        n += c["sent"] + c["failed"]
    return f"{n} processed"


@every(15)
@task("ict.retry_meta_events")
def retry_meta_events():
    """Lead-form deliveries whose details couldn't be fetched from Meta are tried again (up to 5 times)."""
    from .meta import retry_failed

    return f"{retry_failed()} recovered"


@every(60 * 6)
@task("ict.json_backup_due")
def json_backup_due():
    """Prepares a portable backup when the chosen frequency says one is due (checked a few times a day)."""
    from . import backups

    return f"backup {backups.create_backup().pk}" if backups.is_due() else "not due"


@every(5)
@task("ict.retry_whatsapp")
def retry_whatsapp():
    """Failed WhatsApp messages are tried again (2, 10, 60, 240 minutes later, up to the number set in ICT → WhatsApp)."""
    from apps.integrations.messaging import retry_due

    return f"{retry_due()} resent"
