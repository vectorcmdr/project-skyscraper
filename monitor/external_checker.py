"""External site monitoring -- DNS, robots.txt, content changes for third-party sites."""

import hashlib
import json
import re
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone

from monitor.config import EXTERNAL_SITES, BASE_URL, MIRROR_DIR
from monitor.http_client import fetch, jitter
from monitor.url_mapper import url_to_path
from monitor.logger import log
from monitor.noise_filter import strip_page_noise, is_noise_diff_line
from monitor.sitemap import _parse_sitemap_urls


SITE_LABELS = {
    "wakingtitan.com": "wakingtitan",
    "recalldreams.dev": "recalldreams",
}


def check_external_sites(state: dict) -> list:
    changes = []
    ext_state = state.setdefault("external", {})

    for hostname, info in EXTERNAL_SITES.items():
        site_state = ext_state.setdefault(hostname, {})
        site_label = SITE_LABELS.get(hostname, hostname.split(".")[0])

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
        elif hostname == "freeimage.host":
            try:
                c = _check_freeimage_user(info["url"], hostname, site_state, site_label)
                changes.extend(c)
            except Exception as e:
                log(f"  External freeimage check failed for {hostname}: {e}", "ERROR")
        else:
            try:
                c = _check_generic_site(info["url"], hostname, site_state, site_label)
                changes.extend(c)
            except Exception as e:
                log(f"  External content check failed for {hostname}: {e}", "ERROR")

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
    new_urls = _parse_sitemap_urls(content)

    added = set(new_urls.keys()) - set(old_urls.keys())
    removed = set(old_urls.keys()) - set(new_urls.keys())

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

    result = fetch(api_url, etag=api_state.get("etag"))
    if result.not_modified:
        api_state["last_checked"] = datetime.now(timezone.utc).isoformat()
        return []

    items, new_hash, total_pages, new_etag, _ = _fetch_all_pages(api_url)

    if not items:
        return []

    known_items = {}
    if isinstance(api_state.get("items"), list):
        for i in api_state["items"]:
            known_items[str(i["id"])] = i

    known_ids = set(known_items.keys())
    new_ids = set()
    new_items_map = {}

    for item in items:
        iid = str(item.get("id"))
        if iid:
            new_ids.add(iid)
            summary = _item_summary(item, endpoint)
            summary["content_hash"] = _compute_content_hash(item)
            new_items_map[iid] = summary

    changes = []

    added_ids = new_ids - known_ids
    if added_ids:
        for iid in sorted(added_ids):
            item = new_items_map[iid]
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

    removed_ids = known_ids - new_ids
    if removed_ids:
        for iid in sorted(removed_ids):
            known_item = known_items[iid]
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
        new_hash = new_item.get("content_hash", "")
        old_hash = old_item.get("content_hash", "")
        if new_hash and old_hash and new_hash != old_hash:
            changed_items.append((iid, old_item, new_item))

    if changed_items:
        for iid, _, new_item in changed_items[:30]:
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

    api_state["etag"] = new_etag or result.etag
    api_state["hash"] = new_hash
    api_state["items"] = [new_items_map[iid] for iid in sorted(new_items_map, key=int)]
    api_state["last_checked"] = datetime.now(timezone.utc).isoformat()

    return changes


def _compute_content_hash(item: dict) -> str:
    raw = item.get("content", {}).get("rendered", "")
    if not raw:
        return ""
    stripped = strip_page_noise(raw)
    return hashlib.md5(stripped.encode("utf-8")).hexdigest()


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
                log(f"  {hostname}: Newly published {ep_name} #{pid}", "DEEP")
        jitter(0.08, 0.1)

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

        new_hash = result.hash
        old_hash = page_state.get("hash")

        if old_hash is not None and old_hash != new_hash:
            old_text = ""
            old_path = url_to_path(url, subdir="external")
            if old_path.is_file():
                old_text = old_path.read_text(encoding="utf-8", errors="replace")

            new_text = result.text
            diff = _compute_external_diff(old_text, new_text, url)

            if diff:
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


def _save_external_mirror(url: str, result, hostname: str):
    path = url_to_path(url, subdir="external")
    path.parent.mkdir(parents=True, exist_ok=True)
    if result.content:
        path.write_bytes(result.content)
