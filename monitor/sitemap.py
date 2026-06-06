"""Sitemap checking -- fetch, parse, diff against stored state."""

from datetime import datetime, timezone

from monitor.config import SITEMAP_URL
from monitor.http_client import fetch
from monitor.logger import log


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

    new_urls = _parse_sitemap_urls(result.text)
    old_urls = sm.get("urls", {})

    added = set(new_urls.keys()) - set(old_urls.keys())
    removed = set(old_urls.keys()) - set(new_urls.keys())

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


def _parse_sitemap_urls(content: str, depth: int = 0) -> dict:
    import xml.etree.ElementTree as ET

    urls = {}
    if depth > 1:
        return urls
    try:
        root = ET.fromstring(content)
    except ET.ParseError:
        return urls

    tag = root.tag.split("}")[-1] if "}" in root.tag else root.tag
    ns = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}

    if tag == "sitemapindex":
        for sm_elem in root.findall(".//sm:sitemap", ns):
            loc = sm_elem.find("sm:loc", ns)
            if loc is not None and loc.text:
                sub_result = fetch(loc.text.strip(), timeout=10)
                if sub_result.ok and sub_result.content:
                    sub_content = sub_result.content.decode("utf-8", errors="replace")
                    sub_urls = _parse_sitemap_urls(sub_content, depth=depth + 1)
                    urls.update(sub_urls)
        return urls

    if tag == "urlset":
        for url_elem in root.findall(".//sm:url", ns):
            loc = url_elem.find("sm:loc", ns)
            lastmod = url_elem.find("sm:lastmod", ns)
            if loc is not None and loc.text:
                urls[loc.text.strip()] = {
                    "lastmod": lastmod.text.strip() if lastmod is not None and lastmod.text else None,
                    "type": "page",
                }
    return urls


def get_sitemap_urls(state: dict) -> dict:
    return state.get("sitemap", {}).get("urls", {})
