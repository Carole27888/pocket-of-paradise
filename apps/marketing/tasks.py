from apps.core.jobs import daily, every, task

from . import services


@every(minutes=5)
@task("marketing.response_check")
def response_check():
    return f"{services.reassign_unresponsive()} reassigned"


@every(minutes=15)
@task("marketing.sync_inbox")
def sync_inbox():
    return f"{services.sync_inbox()} messages, {services.publish_due_posts()} posts published"


@daily(hour=5, minute=0)
@task("marketing.sync_stats")
def sync_stats():
    return f"{services.sync_channel_stats()} channels, {services.sync_ad_spend()} ad rows"


@daily(hour=5, minute=30)
@task("marketing.post_ad_spend")
def post_ad_spend():
    return f"{services.post_ad_spend_to_finance()} expenses posted"


@daily(hour=6, minute=15)
@task("marketing.sync_reviews")
def sync_reviews():
    return f"{services.sync_reviews()} reviews, {services.sync_ota_enquiries()} OTA enquiries"


@daily(hour=9, minute=0)
@task("marketing.pretrip_reminders")
def pretrip():
    return f"{services.send_pretrip_reminders()} reminders"


@daily(hour=10, minute=0)
@task("marketing.reengage")
def reengage():
    return f"{services.reengage_lapsed()} re-engagement messages, {services.warn_expiring_tokens()} token warnings"


@daily(hour=6, minute=0)
@task("marketing.blog_fetch")
def blog_fetch():
    """Read new posts from the company website blog (Media -> Blogs)."""
    from . import blogs
    from .models import BlogSource

    source = BlogSource.load()
    if not (source.url and source.is_active):
        return "blog not set up"
    try:
        r = blogs.run(source)
        return f"{r['new']} new, {r['updated']} updated"
    except blogs.BlogError as exc:
        return f"blog not read: {exc}"
