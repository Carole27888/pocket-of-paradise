from apps.core.jobs import task


@task("notifications.deliver")
def deliver(notification_id: int, purpose: str = "", fields: dict | None = None):
    """Send an in-app alert on WhatsApp and/or e-mail, as the person chose."""
    from .services import deliver as _deliver

    return _deliver(notification_id, purpose, fields)
