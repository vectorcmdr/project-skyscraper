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


SITE_LABELS = {
    "wakingtitan.com": "wakingtitan",
    "theskyscraperarchitect-ywvhk.wordpress.com": "tower",
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


def _check_site_dns(hostname: str, site_state: dict, site_label: str = "") -> list:
    changes = []
    dns_state = site_state.setdefault("dns", {})
    records = _resolve_dns(hostname)

    for rtype in ("A", "AAAA", "TXT", "CNAME", "MX", "NS"):
        old = dns_state.get(rtype, [])
        new = records.get(rtype, [])
        if old != new:
            dns_state[rtype] = new
            if old:
                diff_lines = []
                old_set, new_set = set(old), set(new)
                for v in sorted(old_set - new_set):
                    diff_lines.append(f"- {rtype} {v}")
                for v in sorted(new_set - old_set):
                    diff_lines.append(f"+ {rtype} {v}")
                changes.append({
                    "type": "external_dns_changed",
                    "site": hostname,
                    "site_label": site_label,
                    "hostname": hostname,
                    "record_type": rtype,
                    "diff": "\n".join(diff_lines),
                    "detail": f"DNS {rtype} record changed for {hostname}",
                })
                log(f"  DNS {rtype} changed for {hostname}: {' '.join(diff_lines)}", "CHECK")

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
                content, "robots.txt"
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
            "diff": f"+ {content[:2000]}",
            "detail": f"Initial robots.txt capture for {hostname}",
        })

    site_state.setdefault("robots_txt", {})
    site_state["robots_txt"]["hash"] = new_hash
    site_state["robots_txt"]["content"] = content[:5000]
    site_state["robots_txt"]["last_checked"] = datetime.now(timezone.utc).isoformat()

    return changes


def _diff_text(old_text: str, new_text: str, label: str = "") -> str:
    import difflib
    old_lines = old_text.splitlines(keepends=True)
    new_lines = new_text.splitlines(keepends=True)
    diff_iter = difflib.unified_diff(old_lines, new_lines, n=3, lineterm="")
    lines = list(diff_iter)[2:]
    result = "\n".join(lines)
    if len(result) > 2000:
        result = result[:1997] + "..."
    return result


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
            new_items_map[iid] = _item_summary(item, endpoint)

    changes = []

    added_ids = new_ids - known_ids
    if added_ids:
        added_details = [new_items_map[iid] for iid in sorted(added_ids)]
        changes.append({
            "type": "external_content_changed",
            "site": hostname,
            "site_label": site_label,
            "endpoint": endpoint,
            "count": len(added_ids),
            "items": added_details[:30],
            "detail": f"{len(added_ids)} new item(s) in {endpoint} on {hostname}",
        })

    removed_ids = known_ids - new_ids
    if removed_ids:
        changes.append({
            "type": "external_content_changed",
            "site": hostname,
            "site_label": site_label,
            "endpoint": endpoint,
            "count": len(removed_ids),
            "items": [],
            "detail": f"{len(removed_ids)} item(s) removed from {endpoint} on {hostname}",
        })

    changed_items = []
    for iid in new_ids & known_ids:
        new_item = new_items_map[iid]
        old_item = known_items.get(iid, {})
        if new_item.get("modified") and old_item.get("modified") and new_item["modified"] != old_item["modified"]:
            changed_items.append((iid, old_item, new_item))
        elif new_item.get("modified") and not old_item.get("modified"):
            changed_items.append((iid, old_item, new_item))

    if changed_items:
        changes.append({
            "type": "external_content_changed",
            "site": hostname,
            "site_label": site_label,
            "endpoint": endpoint,
            "count": len(changed_items),
            "items": [new_items_map[c[0]] for c in changed_items[:30]],
            "detail": f"{len(changed_items)} item(s) modified in {endpoint} on {hostname}",
        })

    api_state["etag"] = new_etag or result.etag
    api_state["hash"] = new_hash
    api_state["items"] = [new_items_map[iid] for iid in sorted(new_items_map, key=int)]
    api_state["last_checked"] = datetime.now(timezone.utc).isoformat()

    return changes


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
                changes.append({
                    "type": "external_unpublished_detected",
                    "site": hostname,
                    "site_label": site_label,
                    "id": pid,
                    "status": result.status,
                    "endpoint": ep_name,
                    "detail": f"Unpublished {ep_name} #{pid} (HTTP {result.status}) on {hostname}",
                })
                log(f"  {hostname}: Unpublished {ep_name} #{pid} (HTTP {result.status})", "DEEP")
            elif result.status == 200:
                ep_name = "posts" if "/posts/" in url else "pages"
                log(f"  {hostname}: Newly published {ep_name} #{pid}", "DEEP")
        jitter(0.08, 0.1)

    probe_state["position"] = chunk_end + 1
    probe_state["last_probed"] = datetime.now(timezone.utc).isoformat()
    log(f"  {hostname}: Probe checked IDs {probe_pos}-{chunk_end}", "DEEP")

    return changes


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

    old_text = strip_page_noise(old_text)
    new_text = strip_page_noise(new_text)

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


def _save_external_mirror(url: str, result, hostname: str):
    path = url_to_path(url, subdir="external")
    path.parent.mkdir(parents=True, exist_ok=True)
    if result.content:
        path.write_bytes(result.content)
