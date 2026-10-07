"""External site monitoring -- DNS, robots.txt, content changes for third-party sites."""

import hashlib
import json
import re
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from monitor.config import EXTERNAL_SITES, BASE_URL, MIRROR_DIR, EXTERNAL_NOTIFY_DEDUP_WINDOW
from monitor.http_client import fetch, jitter
from monitor.url_mapper import url_to_path
from monitor.logger import log
from monitor.noise_filter import strip_page_noise, is_noise_diff_line, diff_has_real_changes, is_noise_route_index
from monitor.sitemap import _parse_sitemap_urls
from monitor.content_extract import compute_content_diff, extract_content_signature


SITE_LABELS = {
    "wakingtitan.com": "wakingtitan",
    "recalldreams.dev": "recalldreams",
}

_DB_ENTRY_ENDPOINT = "/wp-json/wp/v2/database_entry"

# iili.io short-image keys referenced by recalldreams database_entry content
# (sync screenshots, e.g. "CriG0mP.png"). Archived on detection so rotated
# screenshots survive after the live entry/image is deleted.
_SYNC_IMAGE_RE = re.compile(r"\b([A-Za-z0-9]{5,8})\.(png|jpe?g|gif|webp)\b", re.IGNORECASE)
_SYNC_MEDIA_DIR = MIRROR_DIR / "mirrors" / "recalldreams" / "media" / "sync"
_SYNC_ARCHIVE_RETRY = 3600  # seconds before retrying a failed iili.io fetch

_IMAGE_MAGIC = (
    (b"\x89PNG\r\n\x1a\n", "png"),
    (b"\xff\xd8\xff", "jpg"),
    (b"GIF87a", "gif"),
    (b"GIF89a", "gif"),
    (b"RIFF", "webp"),
)


def check_external_db_entries(state: dict) -> list:
    """Check external WP database_entry endpoints (fast tier, 30s)."""
    changes = []
    ext_state = state.get("external", {})
    for hostname, info in EXTERNAL_SITES.items():
        if info.get("type") != "wordpress":
            continue
        site_state = ext_state.setdefault(hostname, {})
        api_url = f"{info['url'].rstrip('/')}{_DB_ENTRY_ENDPOINT}"
        api_state = site_state.setdefault("api", {}).setdefault(_DB_ENTRY_ENDPOINT, {})
        site_label = SITE_LABELS.get(hostname, hostname.split(".")[0])
        try:
            c = _check_wp_collection(api_url, _DB_ENTRY_ENDPOINT, hostname, api_state, site_state, site_label)
            changes.extend(c)
        except Exception as e:
            log(f"  DB entry check failed for {hostname}: {e}", "ERROR")
    return changes


def check_external_sites(state: dict) -> list:
    changes = []
    ext_state = state.setdefault("external", {})

    for hostname, info in EXTERNAL_SITES.items():
        site_state = ext_state.setdefault(hostname, {})
        site_label = SITE_LABELS.get(hostname, hostname.split(".")[0])

        if hostname == "freeimage.host":
            continue  # checked on the fast tier via check_freeimage()

        try:
            c = _check_site_dns(hostname, site_state, site_label)
            changes.extend(c)
        except Exception as e:
            log(f"  External DNS check failed for {hostname}: {e}", "ERROR")

        try:
            c = _check_site_robots_txt(info["url"], hostname, site_state, site_label)
            changes.extend(c)
        except Exception as e:
            log(f"  External robots.txt check failed for {hostname}: {e}", "ERROR")

        try:
            c = _check_site_sitemap(info["url"], hostname, site_state, site_label)
            changes.extend(c)
        except Exception as e:
            log(f"  External sitemap check failed for {hostname}: {e}", "ERROR")

        if info.get("type") == "wordpress":
            try:
                c = _check_wp_site(info["url"], hostname, site_state, site_label)
                changes.extend(c)
            except Exception as e:
                log(f"  External WP check failed for {hostname}: {e}", "ERROR")
        else:
            try:
                c = _check_generic_site(info["url"], hostname, site_state, site_label)
                changes.extend(c)
            except Exception as e:
                log(f"  External content check failed for {hostname}: {e}", "ERROR")

        site_state["last_checked"] = datetime.now(timezone.utc).isoformat()

    return changes


def check_freeimage(state: dict) -> list:
    """Check the freeimage.host gallery (fast tier, 30s)."""
    changes = []
    info = EXTERNAL_SITES.get("freeimage.host")
    if not info:
        return changes
    site_state = state.setdefault("external", {}).setdefault("freeimage.host", {})
    site_label = SITE_LABELS.get("freeimage.host", "freeimage.host".split(".")[0])
    try:
        c = _check_freeimage_user(info["url"], "freeimage.host", site_state, site_label)
        changes.extend(c)
    except Exception as e:
        log(f"  External freeimage check failed: {e}", "ERROR")
    site_state["last_checked"] = datetime.now(timezone.utc).isoformat()
    return changes


def _check_site_dns(hostname: str, site_state: dict, site_label: str = "") -> list:
    changes = []
    dns_state = site_state.setdefault("dns", {})
    records = _resolve_dns(hostname)

    for rtype in ("A", "AAAA", "TXT", "CNAME", "MX", "NS"):
        old = dns_state.get(rtype, [])
        new = records.get(rtype, [])
        if old != new:
            dns_state[rtype] = new
            diff_lines = []
            old_set, new_set = set(old), set(new)
            for v in sorted(old_set - new_set):
                diff_lines.append(f"- {rtype} {v}")
            for v in sorted(new_set - old_set):
                diff_lines.append(f"+ {rtype} {v}")
            caption = "captured" if not old else "changed"
            changes.append({
                "type": "external_dns_changed",
                "site": hostname,
                "site_label": site_label,
                "hostname": hostname,
                "record_type": rtype,
                "diff": "\n".join(diff_lines),
                "detail": f"DNS {rtype} {caption} for {hostname}",
            })
            log(f"  DNS {rtype} {caption} for {hostname}: {' '.join(diff_lines)}", "CHECK")

    return changes


def _resolve_dns(hostname: str) -> dict:
    DNS_TYPES = {"A": 1, "AAAA": 28, "TXT": 16, "CNAME": 5, "MX": 15, "NS": 2}
    results = {}
    for rtype, rtype_num in DNS_TYPES.items():
        try:
            url = f"https://dns.google/resolve?name={urllib.parse.quote(hostname)}&type={rtype}"
            req = urllib.request.Request(url, headers={
                "User-Agent": "Mozilla/5.0 (project-skyscraper-monitor/1.0)",
                "Accept": "application/dns-json",
            })
            resp = urllib.request.urlopen(req, timeout=15)
            data = json.loads(resp.read().decode())
            values = []
            for answer in data.get("Answer", []):
                if answer.get("type") != rtype_num:
                    continue
                v = answer.get("data", "")
                if rtype == "MX":
                    v = v.split(" ")[-1] if " " in v else v
                if v:
                    values.append(v)
            results[rtype] = sorted(values)
        except Exception as e:
            log(f"  DNS resolve {hostname} {rtype}: {e}", "DEEP")
            results[rtype] = []
        time.sleep(0.1)
    return results


def _check_site_robots_txt(site_url: str, hostname: str, site_state: dict, site_label: str = "") -> list:
    changes = []
    robots_url = f"{site_url.rstrip('/')}/robots.txt"
    result = fetch(robots_url)

    if result.failed:
        return changes

    content = result.text

    # Skip if redirected to a different domain
    final_host = urllib.parse.urlparse(result.final_url).hostname
    if final_host and final_host not in (hostname, f"www.{hostname}"):
        log(f"  robots.txt for {hostname} redirected to {result.final_url} -- skipping", "WARN")
        return changes

    # Skip if response is HTML (redirect or server error returned a page)
    content_type = result.headers.get("Content-Type", "")
    if "text/html" in content_type:
        log(f"  robots.txt for {hostname} returned HTML (Content-Type: {content_type}) -- skipping", "WARN")
        return changes
    stripped = content.lstrip()
    if stripped.startswith("<!doctype") or stripped.startswith("<html"):
        log(f"  robots.txt for {hostname} appears to be HTML markup -- skipping", "WARN")
        return changes

    # Scrub credential-shaped strings (API keys etc.) before hashing and
    # storing: the content lands in the state file, which is committed on
    # hosted runs, and must never trip secret scanning.
    from monitor.feed_manager import scrub_credentials
    content = scrub_credentials(content)

    new_hash = hashlib.md5(content.encode("utf-8")).hexdigest()
    old_hash = site_state.get("robots_txt", {}).get("hash")

    if old_hash is not None and old_hash != new_hash:
        changes.append({
            "type": "external_robots_txt_changed",
            "site": hostname,
            "site_label": site_label,
            "url": robots_url,
            "diff": _diff_text(
                site_state.get("robots_txt", {}).get("content", ""),
                content
            ),
            "detail": f"robots.txt changed for {hostname}",
        })
        log(f"  robots.txt changed for {hostname}", "CHECK")
    elif old_hash is None:
        changes.append({
            "type": "external_robots_txt_changed",
            "site": hostname,
            "site_label": site_label,
            "url": robots_url,
            "diff": "\n".join("+ " + line for line in content.splitlines()),
            "detail": f"Initial robots.txt capture for {hostname}",
        })

    site_state.setdefault("robots_txt", {})
    site_state["robots_txt"]["hash"] = new_hash
    site_state["robots_txt"]["content"] = content
    site_state["robots_txt"]["last_checked"] = datetime.now(timezone.utc).isoformat()

    return changes


def _check_site_sitemap(site_url: str, hostname: str, site_state: dict, site_label: str = "") -> list:
    changes = []
    sm_state = site_state.setdefault("sitemap", {})
    sm_state.setdefault("urls", {})

    for try_path in ("/sitemap.xml", "/wp-sitemap.xml"):
        sm_url = f"{site_url.rstrip('/')}{try_path}"
        result = fetch(sm_url, etag=sm_state.get("etag"), last_modified=sm_state.get("last_modified"))
        if result.ok and result.content:
            break
        if result.not_modified:
            sm_state["last_checked"] = datetime.now(timezone.utc).isoformat()
            return changes
    else:
        if sm_state.get("hash") and not result.ok:
            log(f"  Sitemap fetch failed for {hostname}: {result.status}", "WARN")
        return changes

    content = result.text
    new_hash = hashlib.md5(content.encode("utf-8")).hexdigest()
    old_hash = sm_state.get("hash")

    old_urls = sm_state.get("urls", {})
    new_urls, complete = _parse_sitemap_urls(content)

    # Same guards as the main sitemap checker: incomplete parses and
    # complete-but-empty indexes (Jetpack buffer rebuild) are transient --
    # keep the baseline, never diff/notify.
    if not complete or (not new_urls and old_urls):
        if not complete:
            log(f"  Sitemap incomplete for {hostname} -- skipping, baseline preserved", "WARN")
        else:
            log(f"  Sitemap empty for {hostname} (rebuild) -- skipping, baseline preserved", "WARN")
        sm_state["last_checked"] = datetime.now(timezone.utc).isoformat()
        return changes

    added = set(new_urls.keys()) - set(old_urls.keys())
    removed = set(old_urls.keys()) - set(new_urls.keys())

    # Quiet baseline restore (empty baseline + full parse)
    if not old_urls and added:
        log(f"  Sitemap baseline restored for {hostname} ({len(new_urls)} URL(s)) -- silent", "CHECK")
        sm_state["etag"] = result.etag
        sm_state["last_modified"] = result.last_modified
        sm_state["hash"] = new_hash
        sm_state["urls"] = new_urls
        sm_state["last_checked"] = datetime.now(timezone.utc).isoformat()
        return changes

    # Mass-removal confirmation: two consecutive observations of the same
    # >50% shrink before notifying + re-baselining.
    if removed and len(removed) > max(10, int(len(old_urls) * 0.5)):
        pending = sm_state.get("_pending_removed")
        if pending == sorted(removed):
            log(f"  Sitemap mass removal confirmed for {hostname} ({len(removed)} URL(s))", "WARN")
            changes.append({
                "type": "external_sitemap_changed",
                "site": hostname,
                "site_label": site_label,
                "url": sm_url,
                "added": [],
                "removed": sorted(removed)[:50],
                "diff": "\n".join("- " + u for u in sorted(removed)[:20]),
                "detail": f"Sitemap: -{len(removed)} URL(s) for {hostname}",
            })
            sm_state["etag"] = result.etag
            sm_state["last_modified"] = result.last_modified
            sm_state["hash"] = new_hash
            sm_state["urls"] = new_urls
            sm_state["_pending_removed"] = None
        else:
            sm_state["_pending_removed"] = sorted(removed)
            log(f"  Sitemap suspicious mass removal for {hostname} ({len(removed)} URL(s)) -- awaiting confirmation", "WARN")
        sm_state["last_checked"] = datetime.now(timezone.utc).isoformat()
        return changes

    sm_state["_pending_removed"] = None

    if added:
        changes.append({
            "type": "external_sitemap_changed",
            "site": hostname,
            "site_label": site_label,
            "url": sm_url,
            "added": sorted(added)[:50],
            "removed": [],
            "diff": "\n".join("+ " + u for u in sorted(added)[:20]),
            "detail": f"Sitemap: +{len(added)} URL(s) for {hostname}",
        })
    if removed:
        changes.append({
            "type": "external_sitemap_changed",
            "site": hostname,
            "site_label": site_label,
            "url": sm_url,
            "added": [],
            "removed": sorted(removed)[:50],
            "diff": "\n".join("- " + u for u in sorted(removed)[:20]),
            "detail": f"Sitemap: -{len(removed)} URL(s) for {hostname}",
        })

    if not added and not removed:
        if old_hash is None:
            log(f"  Sitemap captured for {hostname}", "CHECK")
        else:
            log(f"  Sitemap unchanged for {hostname}", "CHECK")
    else:
        log(f"  Sitemap: +{len(added)} -{len(removed)} for {hostname}", "CHECK")

    sm_state["etag"] = result.etag
    sm_state["last_modified"] = result.last_modified
    sm_state["hash"] = new_hash
    sm_state["urls"] = new_urls
    sm_state["last_checked"] = datetime.now(timezone.utc).isoformat()

    return changes


def _diff_text(old_text: str, new_text: str) -> str:
    import difflib
    old_lines = old_text.splitlines(keepends=True)
    new_lines = new_text.splitlines(keepends=True)
    diff_iter = difflib.unified_diff(old_lines, new_lines, n=3, lineterm="")
    lines = list(diff_iter)[2:]
    return "\n".join(lines)


def _check_wp_site(site_url: str, hostname: str, site_state: dict, site_label: str = "") -> list:
    changes = []

    wp_endpoints = [
        f"/wp-json/wp/v2/posts",
        f"/wp-json/wp/v2/pages",
        f"/wp-json/wp/v2/media",
        f"/wp-json/wp/v2/database_entry",
    ]

    for endpoint in wp_endpoints:
        api_url = f"{site_url.rstrip('/')}{endpoint}"
        api_state = site_state.setdefault("api", {}).setdefault(endpoint, {})

        try:
            c = _check_wp_collection(api_url, endpoint, hostname, api_state, site_state, site_label)
            changes.extend(c)
        except Exception as e:
            log(f"  WP collection check failed for {endpoint} on {hostname}: {e}", "ERROR")

    # Probe for unpublished content
    try:
        c = _external_probe_unpublished(hostname, site_url, site_state, site_label)
        changes.extend(c)
    except Exception as e:
        log(f"  External probe failed for {hostname}: {e}", "ERROR")

    return changes


def _check_wp_collection(api_url: str, endpoint: str, hostname: str,
                         api_state: dict, site_state: dict, site_label: str = "") -> list:
    from monitor.api_collections import _fetch_all_pages, _item_summary

    # Always fetch fresh -- skip ETag conditional GET to avoid stale CDN caches
    items, new_hash, total_pages, new_etag, _ = _fetch_all_pages(api_url)

    if not items:
        return []

    # First run for this endpoint: quiet capture, no notifications
    if "items" not in api_state:
        is_db_entry = "database_entry" in endpoint
        summaries = []
        for item in items:
            s = _item_summary(item, endpoint)
            s["content_hash"] = _compute_content_hash(item)
            if is_db_entry:
                s["content_text"] = _strip_content_html(item)
                _archive_sync_images(item, site_state)
            summaries.append(s)
        api_state["items"] = summaries
        api_state["etag"] = new_etag
        api_state["hash"] = new_hash
        api_state["last_checked"] = datetime.now(timezone.utc).isoformat()
        log(f"  Initial capture for {endpoint} on {hostname}: {len(items)} items", "CHECK")
        return []

    known_items = {}
    if isinstance(api_state.get("items"), list):
        for i in api_state["items"]:
            known_items[str(i["id"])] = i

    known_ids = set(known_items.keys())
    new_ids = set()
    new_items_map = {}
    is_db_entry = "database_entry" in endpoint

    for item in items:
        iid = str(item.get("id"))
        if iid:
            new_ids.add(iid)
            summary = _item_summary(item, endpoint)
            summary["content_hash"] = _compute_content_hash(item)
            if is_db_entry:
                summary["content_text"] = _strip_content_html(item)
                _archive_sync_images(item, site_state)
            new_items_map[iid] = summary

    changes = []

    added_ids = new_ids - known_ids
    if added_ids:
        for iid in sorted(added_ids):
            item = new_items_map[iid]
            if is_db_entry:
                changes.append({
                    "type": "database_entry_changed",
                    "site": hostname,
                    "site_label": site_label,
                    "change_kind": "added",
                    "title": item.get("title", ""),
                    "slug": item.get("slug", ""),
                    "date": item.get("date_gmt", ""),
                    "content": item.get("content_text", ""),
                    "detail": f"Terminal command added: {item.get('title', '')[:120]} on {hostname}",
                })
            else:
                changes.append({
                    "type": "external_content_changed",
                    "site": hostname,
                    "site_label": site_label,
                    "endpoint": endpoint,
                    "url": item.get("link", ""),
                    "detail": f"New {endpoint.rstrip('/').split('/')[-1]}: {item.get('title', '')[:120]} on {hostname}",
                    "diff": f"+ #{item.get('id','?')}: {item.get('title','')[:80]}",
                    "items": [item],
                })
                _mark_sig_notified(site_state, item.get("content_hash", ""))

    removed_ids = known_ids - new_ids
    if removed_ids:
        for iid in sorted(removed_ids):
            known_item = known_items[iid]
            if is_db_entry:
                changes.append({
                    "type": "database_entry_changed",
                    "site": hostname,
                    "site_label": site_label,
                    "change_kind": "removed",
                    "title": known_item.get("title", ""),
                    "slug": known_item.get("slug", ""),
                    "date": known_item.get("date_gmt", ""),
                    "content": known_item.get("content_text", ""),
                    "detail": f"Terminal command removed: {known_item.get('title', '')[:120]} on {hostname}",
                })
            else:
                changes.append({
                    "type": "external_content_changed",
                    "site": hostname,
                    "site_label": site_label,
                    "endpoint": endpoint,
                    "url": known_item.get("link", ""),
                    "detail": f"Removed {endpoint.rstrip('/').split('/')[-1]}: {known_item.get('title', '')[:120]} on {hostname}",
                    "diff": f"- #{known_item.get('id','?')}: {known_item.get('title','')[:80]}",
                    "items": [known_item],
                })

    changed_items = []
    for iid in new_ids & known_ids:
        new_item = new_items_map[iid]
        old_item = known_items.get(iid, {})
        new_hash_val = new_item.get("content_hash", "")
        old_hash_val = old_item.get("content_hash", "")
        if new_hash_val and old_hash_val and new_hash_val != old_hash_val:
            changed_items.append((iid, old_item, new_item))

    if changed_items:
        for iid, _, new_item in changed_items[:30]:
            if is_db_entry:
                changes.append({
                    "type": "database_entry_changed",
                    "site": hostname,
                    "site_label": site_label,
                    "change_kind": "modified",
                    "title": new_item.get("title", ""),
                    "slug": new_item.get("slug", ""),
                    "date": new_item.get("date_gmt", ""),
                    "content": new_item.get("content_text", ""),
                    "detail": f"Terminal command modified: {new_item.get('title', '')[:120]} on {hostname}",
                })
            else:
                sig = new_item.get("content_hash", "")
                if _sig_notified(site_state, sig):
                    log(f"  {endpoint} item {iid} on {hostname}: content already notified (dedup window) -- suppressed", "CHECK")
                    continue
                changes.append({
                    "type": "external_content_changed",
                    "site": hostname,
                    "site_label": site_label,
                    "endpoint": endpoint,
                    "url": new_item.get("link", ""),
                    "detail": f"Modified {endpoint.rstrip('/').split('/')[-1]}: {new_item.get('title', '')[:120]} on {hostname}",
                    "diff": f"~ #{new_item.get('id','?')}: {new_item.get('title','')[:80]}",
                    "items": [new_item],
                })
                _mark_sig_notified(site_state, sig)

    api_state["etag"] = new_etag
    api_state["hash"] = new_hash
    api_state["items"] = [new_items_map[iid] for iid in sorted(new_items_map, key=int)]
    api_state["last_checked"] = datetime.now(timezone.utc).isoformat()

    return changes


def iili_image_refs(text: str) -> list:
    """Extract (filename, url) pairs for iili.io sync image names in a text string.

    Deduplicated by key. Used both for archival and for embedding images in
    RecallDreams terminal notifications.
    """
    refs = {}
    for m in _SYNC_IMAGE_RE.finditer(text or ""):
        key = m.group(1)
        ext = m.group(2).lower()
        if key not in refs:
            refs[key] = (f"{key}.{ext}", f"https://iili.io/{key}.{ext}")
    return list(refs.values())


def _sync_image_refs(item: dict) -> list:
    """Extract (filename, url) pairs for iili.io sync images referenced in a database_entry item.

    Returns a list of (filename, url) tuples, deduplicated by key, so callers can
    archive each referenced screenshot before the live entry/image is rotated away.
    """
    raw = item.get("content", {})
    html = raw.get("rendered", "") if isinstance(raw, dict) else ""
    return iili_image_refs(f"{html}\n{item.get('content_text', '')}")


def _is_image_bytes(data: bytes) -> bool:
    return any(data.startswith(magic) for magic, _ in _IMAGE_MAGIC)


def grab_sync_images(refs: list, site_state: dict) -> list:
    """Download iili.io sync images for a list of (filename, url) refs.

    Stores each image under mirrors/recalldreams/media/sync/<key>.<ext> and tracks
    archive state (per-site) so already-archived files are skipped and failed
    fetches are retried after _SYNC_ARCHIVE_RETRY seconds. Returns the list of
    (filename, url) refs that are archived and reachable (empty when nothing is).
    """
    archived = []
    archive_state = site_state.setdefault("sync_archive", {})
    now = datetime.now(timezone.utc).isoformat()

    for filename, url in refs:
        rec = archive_state.get(filename)
        if rec and rec.get("status") == "archived":
            archived.append((filename, url))
            continue
        if rec and rec.get("status") == "failed":
            last = rec.get("last_attempt", "")
            try:
                last_dt = datetime.fromisoformat(last.replace("Z", "+00:00"))
                if (datetime.now(timezone.utc) - last_dt).total_seconds() < _SYNC_ARCHIVE_RETRY:
                    continue
            except Exception:
                pass

        result = fetch(url, timeout=15)
        if result.ok and result.content and _is_image_bytes(result.content):
            target = _SYNC_MEDIA_DIR / filename
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(result.content)
            except Exception as e:
                log(f"  Sync image write failed {filename}: {e}", "ERROR")
                archive_state[filename] = {
                    "status": "failed", "url": url,
                    "last_attempt": now, "error": str(e),
                }
                continue
            archive_state[filename] = {
                "status": "archived", "url": url,
                "path": str(target.relative_to(MIRROR_DIR)),
                "bytes": len(result.content),
                "archived_at": now,
            }
            archived.append((filename, url))
            log(f"  Sync image archived: {filename} ({len(result.content)} bytes)", "CHECK")
        else:
            archive_state[filename] = {
                "status": "failed", "url": url,
                "last_attempt": now,
                "status_code": result.status,
                "error": result.error,
            }
            log(f"  Sync image fetch failed: {filename} (status {result.status})", "WARN")

    return archived


def _archive_sync_images(item: dict, site_state: dict) -> list:
    """Download iili.io sync screenshots referenced by a database_entry item."""
    return [filename for filename, _ in grab_sync_images(_sync_image_refs(item), site_state)]


def _compute_content_hash(item: dict) -> str:
    raw = item.get("content", {}).get("rendered", "")
    if not raw:
        return ""
    stripped = strip_page_noise(raw)
    return hashlib.md5(stripped.encode("utf-8")).hexdigest()


def _iso_epoch(ts) -> float:
    if not ts:
        return 0.0
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def _notified_sigs(site_state: dict) -> dict:
    """Registry of already-notified content signatures for a site.

    Keyed on the noise-stripped NEW content (not the URL), so the same
    underlying change is reported once regardless of which URL
    representation (collection JSON, item JSON, HTML page) or which
    checker path surfaces it first. Entries older than the dedup window
    are pruned on access.
    """
    reg = site_state.setdefault("notified_sigs", {})
    cutoff = datetime.now(timezone.utc).timestamp() - EXTERNAL_NOTIFY_DEDUP_WINDOW
    stale = [s for s, ts in reg.items() if _iso_epoch(ts) < cutoff]
    for s in stale:
        del reg[s]
    return reg


def _sig_notified(site_state: dict, sig: str) -> bool:
    if not sig:
        return False
    ts = site_state.get("notified_sigs", {}).get(sig)
    if not ts:
        return False
    return (datetime.now(timezone.utc).timestamp() - _iso_epoch(ts)) < EXTERNAL_NOTIFY_DEDUP_WINDOW


def _mark_sig_notified(site_state: dict, sig: str):
    if not sig:
        return
    _notified_sigs(site_state)[sig] = datetime.now(timezone.utc).isoformat()


def _strip_content_html(item: dict) -> str:
    raw = item.get("content", {})
    html = raw.get("rendered", "") if isinstance(raw, dict) else ""
    if not html:
        return ""
    text = re.sub(r'<[^>]+>', ' ', html)
    text = re.sub(r'\s+', ' ', text).strip()
    return text[:1000]


def _external_probe_unpublished(hostname: str, site_url: str, site_state: dict, site_label: str = "") -> list:
    from monitor.config import PROBE_RANGE, PROBE_CHUNK_SIZE
    from monitor.http_client import head_url, jitter

    changes = []
    probe_state = site_state.setdefault("probe", {})

    max_id = 0
    for ep in ("/wp-json/wp/v2/posts", "/wp-json/wp/v2/pages"):
        api_state = site_state.get("api", {}).get(ep, {})
        for item in api_state.get("items", []):
            iid = item.get("id", 0)
            if iid and iid > max_id:
                max_id = iid

    if max_id == 0:
        probe_state["position"] = probe_state.get("position", 3)
        max_id = probe_state.get("max_seen", 20)

    unpublished_log = probe_state.setdefault("unpublished", {"posts": [], "pages": []})
    _migrate_external_log(unpublished_log)
    seen_posts = {_entry_id(e) for e in unpublished_log["posts"]}
    seen_pages = {_entry_id(e) for e in unpublished_log["pages"]}

    probe_pos = probe_state.get("position", max_id + 1)
    probe_ceiling = max_id + PROBE_RANGE
    if probe_pos > probe_ceiling:
        probe_pos = max_id + 1
    chunk_end = min(probe_pos + PROBE_CHUNK_SIZE - 1, probe_ceiling)

    for pid in range(probe_pos, chunk_end + 1):
        for ep_template in ["/wp-json/wp/v2/posts/{id}", "/wp-json/wp/v2/pages/{id}"]:
            url = f"{site_url.rstrip('/')}{ep_template.replace('{id}', str(pid))}"
            result = head_url(url)
            if result.status in (401, 403):
                ep_name = "posts" if "/posts/" in url else "pages"
                seen_set = seen_posts if ep_name == "posts" else seen_pages
                if pid not in seen_set:
                    changes.append({
                        "type": "external_unpublished_detected",
                        "site": hostname,
                        "site_label": site_label,
                        "id": pid,
                        "status": result.status,
                        "endpoint": ep_name,
                        "detail": f"Unpublished {ep_name} #{pid} (HTTP {result.status}) on {hostname}",
                    })
                    seen_set.add(pid)
                    unpublished_log[ep_name].append({
                        "id": pid,
                        "first_seen": datetime.now(timezone.utc).isoformat(),
                    })
                    log(f"  {hostname}: Unpublished {ep_name} #{pid} (HTTP {result.status})", "DEEP")
                else:
                    log(f"  {hostname}: Unpublished {ep_name} #{pid} (already known)", "DEEP")
            elif result.status == 200:
                ep_name = "posts" if "/posts/" in url else "pages"
                found_entry = None
                remaining = []
                for e in unpublished_log[ep_name]:
                    if _entry_id(e) == pid:
                        found_entry = e
                    else:
                        remaining.append(e)
                unpublished_log[ep_name] = remaining
                first_seen = found_entry.get("first_seen", "") if isinstance(found_entry, dict) else ""
                changes.append({
                    "type": "external_unpublished_to_published",
                    "site": hostname,
                    "site_label": site_label,
                    "id": pid,
                    "endpoint": ep_name,
                    "first_seen": first_seen,
                    "detail": f"Previously unpublished {ep_name} #{pid} is now public on {hostname}",
                })
                recently = probe_state.setdefault("recently_published", {})
                recently[str(pid)] = {
                    "first_seen": first_seen,
                    "published_at": datetime.now(timezone.utc).isoformat(),
                    "endpoint": ep_name,
                }
                log(f"  {hostname}: Newly published {ep_name} #{pid}", "DEEP")
        jitter(0.08, 0.1)

    # Clean up recently_published entries older than 2 hours
    recently = probe_state.get("recently_published", {})
    now = datetime.now(timezone.utc)
    stale = [k for k, v in recently.items()
             if (now - datetime.fromisoformat(v.get("published_at", now.isoformat()).replace("Z", "+00:00"))).total_seconds() > 7200]
    for k in stale:
        del recently[k]

    probe_state["position"] = chunk_end + 1
    probe_state["last_probed"] = datetime.now(timezone.utc).isoformat()
    log(f"  {hostname}: Probe checked IDs {probe_pos}-{chunk_end}", "DEEP")

    return changes


def _entry_id(entry):
    if isinstance(entry, dict):
        return entry.get("id", 0)
    if isinstance(entry, (list, tuple)) and len(entry) > 0:
        return entry[0]
    return 0


def _migrate_external_log(ulog):
    changed = False
    for ep in ("posts", "pages"):
        new_list = []
        for entry in ulog.get(ep, []):
            if isinstance(entry, (list, tuple)):
                pid = entry[0] if len(entry) > 0 else 0
                if pid:
                    new_list.append({"id": pid, "first_seen": datetime.now(timezone.utc).isoformat()})
                    changed = True
            else:
                new_list.append(entry)
        ulog[ep] = new_list
    if changed:
        log("Migrated external unpublished log entries to dict format", "FILE")


def _check_generic_site(site_url: str, hostname: str, site_state: dict, site_label: str = "") -> list:
    changes = []
    pages_state = site_state.setdefault("pages", {})

    urls_to_check = [site_url.rstrip("/") + "/"]

    for url in urls_to_check:
        page_state = pages_state.setdefault(url, {})
        result = fetch(url, etag=page_state.get("etag"), last_modified=page_state.get("last_modified"))

        if result.not_modified:
            continue

        if result.failed:
            log(f"  {hostname}: fetch failed ({result.status})", "WARN")
            continue

        # Skip if redirected to a different domain (CDN cache contamination)
        final_host = urllib.parse.urlparse(result.final_url).hostname
        if final_host and final_host not in (hostname, f"www.{hostname}"):
            log(f"  {hostname}: {url} redirected to {result.final_url} -- skipping", "WARN")
            continue

        new_hash = result.hash
        old_hash = page_state.get("hash")

        if old_hash is not None and old_hash != new_hash:
            old_text = ""
            old_path = url_to_path(url, subdir="external")
            if old_path.is_file():
                old_text = old_path.read_text(encoding="utf-8", errors="replace")

            new_text = result.text
            diff = _compute_external_diff(old_text, new_text, url)

            if diff and diff_has_real_changes(diff):
                changes.append({
                    "type": "external_content_changed",
                    "site": hostname,
                    "site_label": site_label,
                    "url": url,
                    "diff": diff,
                    "detail": f"Content changed: {url}",
                })
                log(f"  Content changed for {url}", "CHECK")

        _save_external_mirror(url, result, hostname)
        page_state["etag"] = result.etag
        page_state["last_modified"] = result.last_modified
        page_state["hash"] = new_hash
        page_state["last_checked"] = datetime.now(timezone.utc).isoformat()

    return changes


def _compute_external_diff(old_text: str, new_text: str, url: str) -> str:
    import difflib

    if is_noise_route_index(url):
        return ""

    old_text = strip_page_noise(old_text).rstrip()
    new_text = strip_page_noise(new_text).rstrip()

    old_lines = old_text.splitlines(keepends=True)
    new_lines = new_text.splitlines(keepends=True)

    diff_iter = difflib.unified_diff(old_lines, new_lines, n=3, lineterm="")
    diff_lines = list(diff_iter)[2:]

    if not diff_lines:
        return ""

    filtered = [l for l in diff_lines if not is_noise_diff_line(l)]
    if not filtered:
        return ""

    result_lines = []
    for l in filtered:
        if l.strip() and not l.strip().startswith("@@"):
            result_lines.append(l)

    if not result_lines:
        return ""

    result = "\n".join(result_lines)
    if len(result) > 2000:
        result = result[:1997] + "..."
    return result


def _check_freeimage_user(page_url: str, hostname: str, site_state: dict, site_label: str = "") -> list:
    changes = []
    fi_state = site_state.setdefault("images", {})

    result = fetch(page_url, etag=fi_state.get("etag"), last_modified=fi_state.get("last_modified"))

    if result.not_modified:
        return changes

    if result.failed:
        log(f"  {hostname}: fetch failed ({result.status})", "WARN")
        return changes

    text = result.text

    # Parse image count
    count_match = re.search(r'data-text="image-count"[^>]*>(\d+)', text)
    new_count = int(count_match.group(1)) if count_match else 0
    old_count = fi_state.get("image_count")

    # Parse individual image entries (visible in gallery)
    new_ids = set()
    image_details = []
    for item in re.finditer(
        r'<div class="list-item[^"]*"\s+data-id="([^"]+)"[^>]*data-title="([^"]*)"[^>]*data-privacy="([^"]*)"',
        text
    ):
        img_id = item.group(1)
        title = item.group(2)
        privacy = item.group(3)
        new_ids.add(img_id)
        image_details.append({"id": img_id, "title": title, "privacy": privacy})

    old_ids = set(fi_state.get("known_image_ids", []))

    # First run: store current state without generating changes
    if old_count is None:
        fi_state["image_count"] = new_count
        fi_state["known_image_ids"] = list(new_ids)
        fi_state["image_details"] = image_details
        fi_state["etag"] = result.etag
        fi_state["last_modified"] = result.last_modified
        fi_state["last_checked"] = datetime.now(timezone.utc).isoformat()
        log(f"  {hostname}: initialised with {new_count} images ({len(new_ids)} visible)", "CHECK")
        return changes

    count_changed = new_count != old_count
    new_image_ids = new_ids - old_ids if new_ids else set()
    ids_changed = bool(new_image_ids)

    if not count_changed and not ids_changed:
        fi_state["etag"] = result.etag
        fi_state["last_modified"] = result.last_modified
        fi_state["last_checked"] = datetime.now(timezone.utc).isoformat()
        return changes

    # Build change detail and diff
    new_images_detail = []
    for d in image_details:
        if d["id"] in new_image_ids or (count_changed and d["id"] not in old_ids):
            new_images_detail.append(f'{d["title"]} (https://iili.io/{d["id"]}.jpg)')
        elif not old_ids:
            new_images_detail.append(f'{d["title"]} (https://iili.io/{d["id"]}.jpg)')

    if new_images_detail:
        detail = f"New image(s) on freeimage.host ({new_count} total)"
        diff_lines = [f"+ {d}" for d in new_images_detail]
        diff = "\n".join(diff_lines)
    elif count_changed and not new_image_ids:
        detail = f"freeimage.host image count: {old_count} → {new_count}"
        diff = f"- count: {old_count}\n+ count: {new_count}"
    else:
        detail = f"freeimage.host updated ({new_count} total)"
        diff = ""

    changes.append({
        "type": "external_content_changed",
        "site": hostname,
        "site_label": site_label,
        "url": page_url,
        "diff": diff,
        "detail": detail,
    })

    log(f"  {hostname}: {detail}", "CHECK")

    fi_state["image_count"] = new_count
    fi_state["known_image_ids"] = list(new_ids)
    fi_state["image_details"] = image_details
    fi_state["etag"] = result.etag
    fi_state["last_modified"] = result.last_modified
    fi_state["last_checked"] = datetime.now(timezone.utc).isoformat()

    return changes


WP_PAGE_CHECK_CHUNK = 5


def check_external_wp_pages(state: dict, chunk_size: int = WP_PAGE_CHECK_CHUNK) -> list:
    """Check external WP site full HTML pages for changes.

    Uses conditional GET + round-robin. The fast tier calls this every
    30s; the recalldreams server rate-limits aggressive polling and
    serves rotating cache variants, so the run is gated to at most once
    every EXTERNAL_WP_PAGES_INTERVAL seconds (state-tracked).
    """
    from monitor.config import EXTERNAL_WP_PAGES_INTERVAL

    changes = []
    ext_state = state.get("external", {})
    last = ext_state.get("_wp_pages_last_check")
    if last:
        try:
            elapsed = (datetime.now(timezone.utc) - datetime.fromisoformat(last)).total_seconds()
            if elapsed < EXTERNAL_WP_PAGES_INTERVAL:
                return []
        except Exception:
            pass
    site_label_map = SITE_LABELS

    for hostname, site_state in ext_state.items():
        info = EXTERNAL_SITES.get(hostname)
        if not info or info.get("type") != "wordpress":
            continue

        site_url = info.get("url", f"https://{hostname}")
        site_label = site_label_map.get(hostname, hostname.split(".")[0])

        # Collect page URLs from the WP pages API state
        page_urls = []
        api_state = site_state.get("api", {}).get("/wp-json/wp/v2/pages", {})
        for item in api_state.get("items", []):
            link = item.get("link", "")
            if link and link.startswith("http"):
                page_urls.append(link)

        # Always include the homepage
        homepage = site_url.rstrip("/") + "/"
        if homepage not in page_urls:
            page_urls.insert(0, homepage)

        if not page_urls:
            continue

        # Round-robin: pick a chunk of pages each cycle
        pages_state = site_state.setdefault("pages", {})
        offset = pages_state.get("_rr_offset", 0)
        chunk = page_urls[offset:offset + chunk_size]
        pages_state["_rr_offset"] = (offset + chunk_size) % len(page_urls)

        for url in chunk:
            page_state = pages_state.setdefault(url, {})

            result = fetch(url, etag=page_state.get("etag"),
                          last_modified=page_state.get("last_modified"))

            if result.not_modified:
                continue
            if result.failed:
                err = f" - {result.error}" if result.error else ""
                log(f"  WP page fetch failed for {url} ({result.status}){err}", "WARN")
                continue

            # Skip if redirected to a different domain (CDN cache contamination)
            final_host = urllib.parse.urlparse(result.final_url).hostname
            if final_host and final_host not in (hostname, f"www.{hostname}"):
                log(f"  WP page {url} redirected to {result.final_url} -- skipping", "WARN")
                continue

            new_text = result.text
            new_hash = result.hash

            # Skip responses that don't appear to be complete HTML
            # Truncated responses cause cascading false-positive notifications
            if result.content and len(result.content) > 100 and '</html>' not in new_text:
                log(f"  Skipping incomplete WP page response for {url} ({len(result.content)} bytes, missing </html>)", "WARN")
                continue
            new_sig = extract_content_signature(new_text)
            new_sig_hash = hashlib.sha256(new_sig.encode()).hexdigest()
            old_sig_hash = page_state.get("_content_hash")

            cooldown_ok = True
            last_notified = page_state.get("_notified_at")
            if last_notified:
                elapsed = (datetime.now(timezone.utc) - datetime.fromisoformat(last_notified)).total_seconds()
                if elapsed < 300:
                    cooldown_ok = False

            commit = True
            if old_sig_hash is not None and old_sig_hash != new_sig_hash:

                # VISIBLE CONTENT changed. Two-strike confirmation: cache
                # variants that flip visible content never confirm; real
                # content changes persist and confirm on the second
                # consecutive observation of the same signature hash.
                # Infrastructure-only churn (CSS compilations, tokens,
                # nonces, classes, inline styles) yields identical
                # signatures and never reaches this branch.
                pending = page_state.get("_pending_content")

                old_text = ""
                old_path = url_to_path(url, subdir="external")
                if old_path.is_file():
                    old_text = old_path.read_text(encoding="utf-8", errors="replace")

                if _baseline_is_corrupt(old_text):
                    # Stored baseline is empty/a tiny stub/an HTTP error page
                    # (corrupt write, partial fetch, artifact) -- heal the
                    # mirror silently: re-baseline without pending/notify so
                    # a corrupt baseline can never produce a dud notification.
                    log(f"  WP page {url}: corrupt baseline ({len(old_text)} bytes) -- silent heal", "WARN")
                    page_state["_pending_content"] = None
                else:
                    old_sig = extract_content_signature(old_text) if old_text else ""
                    diff = compute_content_diff(old_sig, new_sig)

                    if diff and diff_has_real_changes(diff):
                        if pending == new_sig_hash:
                            if cooldown_ok:
                                if _sig_notified(site_state, new_sig_hash):
                                    log(f"  WP page content changed (confirmed, dedup suppressed): {url}", "CHECK")
                                    page_state["_pending_content"] = None
                                else:
                                    changes.append({
                                        "type": "external_content_changed",
                                        "site": hostname,
                                        "site_label": site_label,
                                        "url": url,
                                        "diff": diff,
                                        "detail": f"Content changed: {url}",
                                    })
                                    _mark_sig_notified(site_state, new_sig_hash)
                                    page_state["_notified_at"] = datetime.now(timezone.utc).isoformat()
                                    log(f"  WP page content changed (confirmed) for {url}", "CHECK")
                                    log(f"  DIFF (first 200): {diff[:200]}", "CHECK")
                                    page_state["_pending_content"] = None
                            else:
                                log(f"  WP page content changed (confirmed, cooldown): {url}", "DEEP")
                        else:
                            page_state["_pending_content"] = new_sig_hash
                            log(f"  WP page content changed (pending confirmation): {url}", "DEEP")
                            commit = False
                    else:
                        # Signatures identical or diff classified noise --
                        # infrastructure churn; absorb as baseline.
                        page_state["_pending_content"] = None

            elif old_sig_hash is None:
                # First time with signature tracking -- check for a
                # pre-existing mirror copy and baseline from its signature
                old_text = ""
                old_path = url_to_path(url, subdir="external")
                if old_path.is_file():
                    old_text = old_path.read_text(encoding="utf-8", errors="replace")
                else:
                    # Check the full HTML mirror for this host
                    mirror_root = Path(__file__).resolve().parent.parent / "mirrors" / hostname / "html"
                    if mirror_root.is_dir():
                        parsed = urllib.parse.urlparse(url)
                        p = parsed.path.rstrip("/") or "/"
                        if not Path(p).suffix:
                            p = p.rstrip("/") + "/index.html"
                        candidate = mirror_root / p.lstrip("/")
                        if candidate.is_file():
                            old_text = candidate.read_text(encoding="utf-8", errors="replace")

                if _baseline_is_corrupt(old_text):
                    log(f"  WP page {url}: corrupt baseline ({len(old_text)} bytes) -- silent heal", "WARN")
                else:
                    old_sig = extract_content_signature(old_text) if old_text else ""
                    diff = compute_content_diff(old_sig, new_sig)

                    if diff and diff_has_real_changes(diff):
                        if _sig_notified(site_state, new_sig_hash):
                            log(f"  WP page content changed (captured, dedup suppressed): {url}", "CHECK")
                        else:
                            changes.append({
                                "type": "external_content_changed",
                                "site": hostname,
                                "site_label": site_label,
                                "url": url,
                                "diff": diff,
                                "detail": f"Content changed (captured from mirror): {url}",
                            })
                            _mark_sig_notified(site_state, new_sig_hash)
                            log(f"  WP page content changed (captured from mirror) for {url}", "CHECK")

            else:
                # Content stable (matches last fetch) -- a flip returned to
                # baseline; clear any pending confirmation.
                page_state["_pending_content"] = None

            if commit:
                _save_external_mirror(url, result, hostname)
                page_state["etag"] = result.etag
                page_state["last_modified"] = result.last_modified
                page_state["hash"] = new_hash
                page_state["_content_hash"] = new_sig_hash
            page_state["last_checked"] = datetime.now(timezone.utc).isoformat()

    ext_state["_wp_pages_last_check"] = datetime.now(timezone.utc).isoformat()
    return changes


def _save_external_mirror(url: str, result, hostname: str):
    path = url_to_path(url, subdir="external")
    path.parent.mkdir(parents=True, exist_ok=True)
    if result.content:
        path.write_bytes(result.content)


def _baseline_is_corrupt(text: str) -> bool:
    """True when the stored mirror baseline is unusable: empty, a tiny stub,
    or an HTTP error page. Such baselines must heal silently, never notify."""
    if not text or not text.strip():
        return True
    if len(text) < 500:
        return True
    head = text[:8192].lower()
    return bool(re.search(
        r'<title>\s*(?:\d{3}\s+(?:too many requests|service unavailable|bad gateway|'
        r'gateway timeout|internal server error|not found|forbidden|unauthorized|'
        r'bad request)|429|403|404|500|502|503|504)\b', head))
