"""Sitemap checking -- fetch, parse, diff against stored state."""

from datetime import datetime, timezone

from monitor.config import SITEMAP_URL
from monitor.http_client import fetch
from monitor.logger import log

# A shrink larger than this fraction of known URLs (or this many URLs)
# must be observed on two consecutive checks before it is reported.
MASS_CHANGE_FRACTION = 0.5
MASS_CHANGE_MIN = 10


def check_sitemap(state: dict) -> list:
    changes = []
    sm = state.setdefault("sitemap", {})
    sm.setdefault("urls", {})

    result = fetch(SITEMAP_URL, etag=sm.get("etag"), last_modified=sm.get("last_modified"))

    if result.not_modified:
        log("Sitemap: unchanged (304)", "FAST")
        sm["last_checked"] = datetime.now(timezone.utc).isoformat()
        return changes

    if result.failed:
        log(f"Sitemap fetch failed: {result.status} {result.error}", "WARN")
        changes.append({
            "type": "sitemap_error",
            "detail": f"HTTP {result.status}: {result.error}",
        })
        return changes

    new_urls, complete = _parse_sitemap_urls(result.text)
    old_urls = sm.get("urls", {})

    # Jetpack serves an EMPTY sitemapindex while rebuilding its buffer, and
    # partial sub-sitemap fetch failures produce unreliable sets. In both
    # cases keep the stored baseline and never diff/notify.
    if not complete or (not new_urls and old_urls):
        if not complete:
            log("Sitemap: incomplete parse (sub-sitemap fetch failed) -- skipping, baseline preserved", "WARN")
        else:
            log("Sitemap: empty index (Jetpack buffer rebuild) -- skipping, baseline preserved", "WARN")
        sm["_pending_removed"] = None
        sm["last_checked"] = datetime.now(timezone.utc).isoformat()
        return changes

    added = set(new_urls.keys()) - set(old_urls.keys())
    removed = set(old_urls.keys()) - set(new_urls.keys())

    # Quiet baseline restore: an empty baseline (e.g. the empty index was
    # stored before this guard existed) + a full parse is a re-baseline,
    # not a mass addition. Store silently, never notify.
    if not old_urls and added:
        log(f"Sitemap: restoring baseline with {len(new_urls)} URL(s) -- silent", "FAST")
        sm["etag"] = result.etag
        sm["last_modified"] = result.last_modified
        sm["last_checked"] = datetime.now(timezone.utc).isoformat()
        sm["urls"] = new_urls
        sm["_pending_removed"] = None
        return changes

    # Mass-removal confirmation: a >50% shrink (or >MASS_CHANGE_MIN URLs)
    # must be observed twice in a row before notifying + re-baselining.
    if removed and len(removed) > max(MASS_CHANGE_MIN, int(len(old_urls) * MASS_CHANGE_FRACTION)):
        pending = sm.get("_pending_removed")
        if pending == sorted(removed):
            log(f"Sitemap: mass removal confirmed ({len(removed)} URL(s))", "WARN")
            changes.append({
                "type": "sitemap_removed",
                "count": len(removed),
                "urls": sorted(removed)[:50],
                "detail": f"Removed {len(removed)} URL(s) from sitemap",
            })
            sm["etag"] = result.etag
            sm["last_modified"] = result.last_modified
            sm["last_checked"] = datetime.now(timezone.utc).isoformat()
            sm["urls"] = new_urls
            sm["_pending_removed"] = None
        else:
            sm["_pending_removed"] = sorted(removed)
            sm["last_checked"] = datetime.now(timezone.utc).isoformat()
            log(f"Sitemap: suspicious mass removal ({len(removed)} URL(s)) -- awaiting confirmation", "WARN")
        return changes

    sm["_pending_removed"] = None

    if added:
        changes.append({
            "type": "sitemap_added",
            "count": len(added),
            "urls": sorted(added)[:50],
            "detail": f"Added {len(added)} URL(s) to sitemap",
        })
    if removed:
        changes.append({
            "type": "sitemap_removed",
            "count": len(removed),
            "urls": sorted(removed)[:50],
            "detail": f"Removed {len(removed)} URL(s) from sitemap",
        })

    if not added and not removed:
        log("Sitemap: content refreshed but no URL changes", "FAST")
    else:
        log(f"Sitemap: +{len(added)} -{len(removed)}", "FAST")

    sm["etag"] = result.etag
    sm["last_modified"] = result.last_modified
    sm["last_checked"] = datetime.now(timezone.utc).isoformat()
    sm["urls"] = new_urls

    return changes


def _parse_sitemap_urls(content: str, depth: int = 0) -> tuple:
    """Parse sitemap XML into (urls dict, complete flag).

    complete=False when the content is not valid sitemap XML or a
    sub-sitemap fetch failed (rate-limited/timeout) -- the URL set is
    unreliable and must NOT be diffed or stored as a new baseline.
    """
    import xml.etree.ElementTree as ET

    urls = {}
    if depth > 1:
        return urls, True
    try:
        root = ET.fromstring(content)
    except ET.ParseError:
        return urls, False

    tag = root.tag.split("}")[-1] if "}" in root.tag else root.tag
    ns = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}

    if tag == "sitemapindex":
        complete = True
        for sm_elem in root.findall(".//sm:sitemap", ns):
            loc = sm_elem.find("sm:loc", ns)
            if loc is not None and loc.text:
                sub_result = fetch(loc.text.strip(), timeout=10)
                if sub_result.ok and sub_result.content:
                    sub_content = sub_result.content.decode("utf-8", errors="replace")
                    sub_urls, sub_complete = _parse_sitemap_urls(sub_content, depth=depth + 1)
                    urls.update(sub_urls)
                    if not sub_complete:
                        complete = False
                else:
                    complete = False
        return urls, complete

    if tag == "urlset":
        for url_elem in root.findall(".//sm:url", ns):
            loc = url_elem.find("sm:loc", ns)
            lastmod = url_elem.find("sm:lastmod", ns)
            if loc is not None and loc.text:
                urls[loc.text.strip()] = {
                    "lastmod": lastmod.text.strip() if lastmod is not None and lastmod.text else None,
                    "type": "page",
                }
        return urls, True

    return urls, False


def get_sitemap_urls(state: dict) -> dict:
    return state.get("sitemap", {}).get("urls", {})