"""Blog scraper: reads the company website's blog into BlogPost rows (Media → Blogs).

Tries, in order: the WordPress REST API, an RSS/Atom feed (advertised on the page or at the usual /feed paths),
then the blog page itself (links to posts, each post's Open Graph tags for title, image, date and summary).
Posts are keyed by URL, so running it again only adds new posts and refreshes changed ones.
Only the standard library is used (no new dependencies).
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime
from email.utils import parsedate_to_datetime
from html import unescape
from html.parser import HTMLParser

from django.utils import timezone
from django.utils.dateparse import parse_datetime

from .models import BlogPost, BlogSource

MAX_BYTES = 3 * 1024 * 1024
MAX_POSTS = 30
UA = "DaysSafarisBlogReader/1.0 (+internal Media dashboard)"
SKIP_PATH = re.compile(r"/(tag|category|author|page|feed|wp-json|wp-admin|login|search)(/|$)|\.(jpg|jpeg|png|gif|pdf|xml|css|js)$", re.I)


class BlogError(Exception):
    pass


def fetch(url: str) -> str:
    """GET a page as text (http/https only, 15 s timeout, 3 MB cap). Tests replace this function."""
    if urllib.parse.urlsplit(url).scheme not in ("http", "https"):
        raise BlogError("The blog address must start with http:// or https://")
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "text/html,application/json,application/xml;q=0.9,*/*;q=0.8"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            data = r.read(MAX_BYTES + 1)
            charset = r.headers.get_content_charset() or "utf-8"
    except urllib.error.HTTPError as exc:
        raise BlogError(f"{url} returned {exc.code}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise BlogError(f"Couldn't reach {url}: {exc}") from exc
    return data[:MAX_BYTES].decode(charset, errors="replace")


def _strip_tags(html: str, limit: int = 400) -> str:
    text = re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", html or ""))).strip()
    return text[:limit].rsplit(" ", 1)[0] + "…" if len(text) > limit else text


def _date(value) -> datetime | None:
    if not value:
        return None
    try:
        dt = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        dt = parse_datetime(str(value).strip().replace("Z", "+00:00"))
        if dt is None and re.match(r"^\d{4}-\d{2}-\d{2}$", str(value).strip()):
            dt = datetime.fromisoformat(str(value).strip())
    if dt is not None and timezone.is_naive(dt):
        dt = timezone.make_aware(dt, timezone.get_default_timezone())
    return dt


# ---------------------------------------------------------------- WordPress

def _wordpress(base: str) -> list[dict]:
    root = "{0.scheme}://{0.netloc}".format(urllib.parse.urlsplit(base))
    try:
        data = json.loads(fetch(f"{root}/wp-json/wp/v2/posts?per_page={MAX_POSTS}&_embed=1"))
    except (BlogError, ValueError):
        return []
    if not isinstance(data, list):
        return []
    posts = []
    for p in data:
        if not isinstance(p, dict) or not p.get("link"):
            continue
        emb = p.get("_embedded") or {}
        media = (emb.get("wp:featuredmedia") or [{}])[0] or {}
        terms = [t.get("name", "") for group in (emb.get("wp:term") or []) for t in group if isinstance(t, dict)]
        posts.append({"url": p["link"], "title": unescape((p.get("title") or {}).get("rendered", "")),
                      "summary": _strip_tags((p.get("excerpt") or {}).get("rendered", "")),
                      "author": ((emb.get("author") or [{}])[0] or {}).get("name", ""), "image_url": media.get("source_url", ""),
                      "categories": ", ".join(t for t in terms if t)[:300], "published_at": _date(p.get("date_gmt") and p["date_gmt"] + "Z" or p.get("date")),
                      "found_via": "wordpress"})
    return posts


# ---------------------------------------------------------------- RSS / Atom

def _feed_urls(base: str, page_html: str) -> list[str]:
    found = re.findall(r'<link[^>]+type=["\']application/(?:rss|atom)\+xml["\'][^>]*>', page_html or "", re.I)
    urls = [urllib.parse.urljoin(base, m.group(1)) for tag in found if (m := re.search(r'href=["\']([^"\']+)', tag))]
    root = "{0.scheme}://{0.netloc}".format(urllib.parse.urlsplit(base))
    urls += [base.rstrip("/") + "/feed/", root + "/feed/", root + "/rss.xml", base.rstrip("/") + "/rss"]
    return list(dict.fromkeys(urls))


def _first(el, paths, ns):
    """First matching child (ElementTree elements without children are falsy, so `or` can't be used)."""
    for path in paths:
        found = el.find(path, ns)
        if found is not None:
            return found
    return None


def _parse_feed(xml_text: str) -> list[dict]:
    try:
        tree = ET.fromstring(xml_text.encode("utf-8") if isinstance(xml_text, str) else xml_text)
    except ET.ParseError:
        return []
    ns = {"atom": "http://www.w3.org/2005/Atom", "media": "http://search.yahoo.com/mrss/", "dc": "http://purl.org/dc/elements/1.1/",
          "content": "http://purl.org/rss/1.0/modules/content/"}
    posts = []
    for item in tree.iter("item"):
        link = (item.findtext("link") or "").strip()
        if not link:
            continue
        html = item.findtext("content:encoded", namespaces=ns) or item.findtext("description") or ""
        img = _first(item, ("media:content", "media:thumbnail", "enclosure"), ns)
        image = img.get("url", "") if img is not None else (re.search(r'<img[^>]+src=["\']([^"\']+)', html) or [None, ""])[1]
        posts.append({"url": link, "title": unescape(item.findtext("title") or "").strip(), "summary": _strip_tags(item.findtext("description") or html),
                      "author": item.findtext("dc:creator", namespaces=ns) or item.findtext("author") or "", "image_url": image or "",
                      "categories": ", ".join(c.text or "" for c in item.findall("category"))[:300],
                      "published_at": _date(item.findtext("pubDate")), "found_via": "rss"})
    for entry in tree.iter("{http://www.w3.org/2005/Atom}entry"):
        link_el = _first(entry, ("atom:link[@rel='alternate']", "atom:link"), ns)
        link = link_el.get("href", "") if link_el is not None else ""
        if not link:
            continue
        posts.append({"url": link, "title": unescape(entry.findtext("atom:title", default="", namespaces=ns)).strip(),
                      "summary": _strip_tags(entry.findtext("atom:summary", default="", namespaces=ns) or entry.findtext("atom:content", default="", namespaces=ns)),
                      "author": entry.findtext("atom:author/atom:name", default="", namespaces=ns), "image_url": "",
                      "categories": "", "published_at": _date(entry.findtext("atom:published", default="", namespaces=ns)
                                                              or entry.findtext("atom:updated", default="", namespaces=ns)),
                      "found_via": "rss"})
    return posts[:MAX_POSTS]


# ---------------------------------------------------------------- HTML page

class _Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links, self.meta, self._a, self._text, self.in_article = [], {}, None, [], 0

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "article":
            self.in_article += 1
        if tag == "a" and a.get("href"):
            self._a, self._text = a["href"], []
        if tag == "meta":
            key = a.get("property") or a.get("name") or a.get("itemprop")
            if key and a.get("content"):
                self.meta.setdefault(key.lower(), a["content"])

    def handle_endtag(self, tag):
        if tag == "article":
            self.in_article = max(0, self.in_article - 1)
        if tag == "a" and self._a:
            self.links.append((self._a, " ".join("".join(self._text).split()), self.in_article > 0))
            self._a = None

    def handle_data(self, data):
        if self._a is not None:
            self._text.append(data)


def _page_posts(base: str, page_html: str) -> list[dict]:
    parser = _Links()
    parser.feed(page_html or "")
    base_parts = urllib.parse.urlsplit(base)
    blog_path = base_parts.path.rstrip("/")
    candidates = []
    for href, text, in_article in parser.links:
        url = urllib.parse.urljoin(base, href).split("#")[0]
        parts = urllib.parse.urlsplit(url)
        if parts.netloc != base_parts.netloc or SKIP_PATH.search(parts.path) or url.rstrip("/") == base.rstrip("/"):
            continue
        under_blog = blog_path and parts.path.startswith(blog_path + "/") and parts.path.rstrip("/") != blog_path
        dated = re.search(r"/20\d\d/\d\d/", parts.path)
        if (in_article or under_blog or dated) and len(text) >= 12:
            candidates.append((url, text))
    posts, seen = [], set()
    for url, text in candidates:
        if url in seen or len(posts) >= MAX_POSTS:
            continue
        seen.add(url)
        post = {"url": url, "title": text, "summary": "", "author": "", "image_url": "", "categories": "", "published_at": None, "found_via": "page"}
        try:
            detail = _Links()
            detail.feed(fetch(url))
            m = detail.meta
            post.update({"title": unescape(m.get("og:title") or text)[:300], "summary": _strip_tags(m.get("og:description") or m.get("description") or ""),
                         "image_url": m.get("og:image", ""), "author": m.get("author", ""),
                         "published_at": _date(m.get("article:published_time") or m.get("datepublished"))})
        except BlogError:
            pass
        posts.append(post)
    return posts


# ---------------------------------------------------------------- Run

def scrape(url: str) -> list[dict]:
    posts = _wordpress(url)
    if posts:
        return posts
    page = ""
    try:
        page = fetch(url)
    except BlogError:
        pass
    for feed_url in _feed_urls(url, page):
        try:
            posts = _parse_feed(fetch(feed_url))
        except BlogError:
            continue
        if posts:
            return posts
    if not page:
        raise BlogError(f"Couldn't open {url}")
    return _page_posts(url, page)


def run(source: BlogSource | None = None) -> dict:
    """Fetch and save. Returns {'new': n, 'updated': n, 'total': n}; errors are kept on the BlogSource."""
    source = source or BlogSource.load()
    if not source.url:
        raise BlogError("Set the blog address first.")
    try:
        posts = scrape(source.url)
    except BlogError as exc:
        source.last_error, source.last_fetched_at = str(exc)[:1000], timezone.now()
        source.save()
        raise
    new = updated = 0
    for p in posts:
        if not p.get("title") or not p.get("url"):
            continue
        fields = {k: (v or "") if k != "published_at" else v for k, v in p.items() if k != "url"}
        fields["title"] = fields["title"][:300]
        obj, created = BlogPost.objects.get_or_create(url=p["url"][:500], defaults=fields)
        if created:
            new += 1
        else:
            changed = {k: v for k, v in fields.items() if v and getattr(obj, k) != v}
            if changed:
                for k, v in changed.items():
                    setattr(obj, k, v)
                obj.save()
                updated += 1
    source.last_fetched_at, source.last_error = timezone.now(), ""
    source.last_result = f"{len(posts)} found · {new} new · {updated} updated"
    source.save()
    return {"new": new, "updated": updated, "total": len(posts)}
