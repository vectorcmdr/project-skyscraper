"""Feed/manifest JSON generation for GitHub Pages site.

Produces docs/data/feed.json (change log) and docs/data/manifest.json
(known pages) sorted newest-first.
"""

import json
import urllib.parse
from datetime import datetime, timezone

from monitor.config import DATA_DIR
from monitor.logger import log
from monitor.api_collections import get_user_map


def generate_site_data(state: dict, changes: list):
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    feed_path = DATA_DIR / "feed.json"
    manifest_path = DATA_DIR / "manifest.json"

    feed = {}
    if feed_path.is_file():
        try:
            feed = json.loads(feed_path.read_text(encoding="utf-8"))
        except Exception:
            feed = {}
    feed.setdefault("entries", [])

    manifest = {}
    if manifest_path.is_file():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception:
            manifest = {}
    manifest.setdefault("pages", [])

    new_entries = []
    for c in changes:
        entry = _change_to_feed_entry(c)
        if entry:
            feed["entries"].append(entry)
            _update_manifest(manifest, c)
            new_entries.append(entry)

    _sync_manifest_from_sitemap(manifest, state)

    feed["entries"] = feed["entries"][-500:]

    user_map = get_user_map(state)
    # Only resolve author names for newly-added entries; existing entries
    # already have resolved string names and would be blanked by re-lookup
    for entry in new_entries:
        aid = entry.get("author", 0)
        if isinstance(aid, int) and aid:
            entry["author"] = user_map.get(aid, "")
    for p in manifest["pages"]:
        aid = p.get("author", 0)
        if isinstance(aid, int) and aid:
            p["author"] = user_map.get(aid, "")

    feed["entries"].sort(key=lambda e: e.get("timestamp", ""), reverse=True)
    manifest["pages"].sort(key=lambda p: p.get("modified", ""), reverse=True)

    feed["updated"] = datetime.now(timezone.utc).isoformat()
    manifest["updated"] = datetime.now(timezone.utc).isoformat()

    _write_if_changed(feed_path, feed, "entries")
    _write_if_changed(manifest_path, manifest, "pages")

    log(f"Site data written: {len(feed['entries'])} feed entries, {len(manifest['pages'])} manifest pages", "FILE")


def generate_external_data(state: dict, changes: list):
    path = DATA_DIR / "external.json"
    data = {"entries": []}
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            data = {"entries": []}
    data.setdefault("entries", [])

    existing_keys = set()
    for e in data["entries"]:
        k = (e.get("type", ""), e.get("site", ""), e.get("detail", "")[:120])
        existing_keys.add(k)

    for c in changes:
        t = c.get("type", "")
        if t.startswith("external_"):
            entry = _change_to_feed_entry(c)
            if entry:
                k = (entry["type"], entry.get("site", ""), entry.get("detail", "")[:120])
                if k not in existing_keys:
                    existing_keys.add(k)
                    data["entries"].append(entry)

    data["entries"] = data["entries"][-500:]
    data["entries"].sort(key=lambda e: e.get("timestamp", ""), reverse=True)
    data["updated"] = datetime.now(timezone.utc).isoformat()

    _write_if_changed(path, data, "entries")
    log(f"External data written: {sum(1 for e in data['entries'] if e['type'].startswith('external_'))} external entries", "FILE")


def seed_feed_from_mirror(state: dict):
    feed_path = DATA_DIR / "feed.json"
    if feed_path.is_file():
        try:
            existing = json.loads(feed_path.read_text(encoding="utf-8"))
            if existing.get("entries"):
                log("Feed already seeded -- skipping", "FILE")
                return
        except Exception:
            pass

    log("Seeding feed from existing mirror data...", "FILE")
    seed_changes = []
    api = state.get("api", {})

    for ep, label in [("/wp-json/wp/v2/pages", "pages"), ("/wp-json/wp/v2/posts", "posts")]:
        for item in api.get(ep, {}).get("items", []):
            seed_changes.append({
                "type": "api_items_added",
                "endpoint": ep,
                "detail": item.get("title", f"Untitled {label}"),
                "items": [item],
            })

    for item in api.get("/wp-json/wp/v2/media", {}).get("items", []):
        seed_changes.append({
            "type": "media_orphan_upload",
            "endpoint": "media",
            "detail": item.get("title", f"Media #{item.get('id', '?')}"),
            "id": item.get("id"),
            "title": item.get("title", "Untitled media"),
            "url": item.get("link", ""),
            "author": item.get("author", 0),
        })

    if seed_changes:
        generate_site_data(state, seed_changes)

    manifest_path = DATA_DIR / "manifest.json"
    if manifest_path.is_file():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception:
            manifest = {"pages": []}
        existing_paths = {p["path"] for p in manifest["pages"]}
        added = 0
        for item in api.get("/wp-json/wp/v2/media", {}).get("items", []):
            path = urllib.parse.urlparse(item.get("link", "")).path
            if path and path not in existing_paths:
                manifest["pages"].append({
                    "path": path,
                    "title": item.get("title", path.split("/")[-1] or path),
                    "type": item.get("type", "attachment"),
                    "modified": item.get("modified") or "",
                    "date_gmt": item.get("date_gmt") or "",
                    "author": item.get("author", 0),
                })
                existing_paths.add(path)
                added += 1
        if added:
            user_map = get_user_map(state)
            for p in manifest["pages"]:
                if p.get("type") == "attachment":
                    aid = p.get("author", 0)
                    p["author"] = user_map.get(aid, "") if aid else ""
            manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
            log(f"Added {added} media items to manifest", "FILE")


def _change_to_feed_entry(c: dict) -> dict | None:
    t = c["type"]
    now = datetime.now(timezone.utc).isoformat()
    link = ""
    title = c.get("detail", "unknown")
    author = 0
    diff = ""

    if t == "sitemap_added":
        urls = c.get("urls", [])
        title = urls[0] if urls else f"{c.get('count', 0)} URL(s) added"
        link = urls[0] if urls else ""
        diff = "\n".join(f"+ {u}" for u in urls[:20])
    elif t == "sitemap_removed":
        urls = c.get("urls", [])
        title = urls[0] if urls else f"{c.get('count', 0)} URL(s) removed"
        link = urls[0] if urls else ""
        diff = "\n".join(f"- {u}" for u in urls[:20])
    elif t == "api_items_added":
        items = c.get("items", [])
        title = items[0].get("title", "") if items else c.get("detail", "")
        link = items[0].get("link", "") if items else ""
        author = items[0].get("author", 0) if items else 0
    elif t == "api_items_removed":
        ids = c.get("ids", [])
        title = c.get("detail", f"{len(ids)} item(s) removed")
    elif t == "api_items_modified":
        items = c.get("items", [])
        title = items[0].get("title", "") if items else c.get("detail", "")
        link = items[0].get("link", "") if items else ""
        author = items[0].get("author", 0) if items else 0
        diffs = c.get("diffs", [])
        if diffs:
            diff = _extract_minimal_diff(diffs)
        if not diff:
            return None
    elif t == "page_content_changed":
        parts = c.get("url", "").rstrip("/").split("/")
        title = parts[-1] if parts and parts[-1] else "page"
        link = c.get("url", "")
        author = c.get("author", 0)
        diffs = c.get("diffs", [])
        diff = _extract_minimal_diff(diffs) if diffs else ""
        if not diff:
            return None
    elif t == "media_replaced":
        title = f"Media #{c.get('id', '?')}"
        link = c.get("new_url", "")
    elif t == "media_thumbnail_changed":
        title = f"Media #{c.get('id', '?')} {c.get('size', '')}"
        link = c.get("url", "")
    elif t == "media_orphan_upload":
        title = c.get("title", f"Media #{c.get('id', '?')}")
        link = c.get("url", "")
        author = c.get("author", 0)
    elif t == "unpublished_detected":
        title = f"#{c.get('id', '?')} ({c.get('endpoint', '')})"
    elif t == "external_dns_changed":
        diff = c.get("diff", "")
        first = diff.split('\n')[0] if diff else ""
        caption = "captured" if diff and not first.startswith('- ') else "changed"
        title = f"DNS {c.get('record_type', '')} {caption} for {c.get('hostname', '')}"
        link = f"https://{c.get('hostname', '')}"
    elif t == "external_robots_txt_changed":
        diff = c.get("diff", "")
        first = diff.split('\n')[0] if diff else ""
        caption = "captured" if diff and not first.startswith('- ') else "changed"
        site = c.get("site", c.get("hostname", ""))
        title = f"robots.txt {caption} for {site}"
        link = c.get("url", f"https://{site}")
        diff = c.get("diff", "")
    elif t == "external_unpublished_detected":
        title = f"#{c.get('id', '?')} ({c.get('endpoint', '')}) on {c.get('hostname', c.get('site', ''))}"
        link = f"https://{c.get('site', '')}/"
        diff = f"HTTP {c.get('status', '?')}"
    elif t.startswith("external_"):
        return None
    else:
        return None

    return {
        "type": t,
        "timestamp": now,
        "title": title,
        "link": link,
        "endpoint": c.get("endpoint", ""),
        "diff": diff,
        "detail": c.get("detail", ""),
        "author": author,
        "site": c.get("site_label", ""),
    }


def _extract_minimal_diff(diffs: list) -> str:
    import re
    from monitor.noise_filter import is_noise_diff_line

    lines_out = []
    for d in diffs:
        text_diff = d.get("text_diff")
        if text_diff:
            for line in text_diff.split("\n"):
                if line.startswith("--- "):
                    continue
                if line and line[0] in ("-", "+"):
                    rest = line[1:].strip()
                    if rest:
                        lines_out.append(f"{line[0]} {rest}")
            continue
        for line in d["diff"].split("\n"):
            line = line.rstrip("\r")
            if not line or line[0] == "@":
                continue
            if is_noise_diff_line(line):
                continue
            prefix = line[0]
            rest = line[1:].strip()
            clean = re.sub(r'<[^>]+>', '', rest)
            if not clean:
                continue
            lines_out.append(f"{prefix} {clean}")

    if not lines_out:
        return ""
    result = "\n".join(lines_out)
    if len(result) > 2000:
        cut = result.rfind("\n", 0, 1997)
        result = result[:cut] if cut > 0 else result[:1997]
        result += "\n... (truncated)"
    return result


def _update_manifest(manifest: dict, c: dict):
    t = c["type"]
    now = datetime.now(timezone.utc).isoformat()
    if t in ("api_items_added", "api_items_modified"):
        for item in c.get("items", []):
            path = urllib.parse.urlparse(item.get("link", "")).path
            if not path:
                continue
            existing = [p for p in manifest["pages"] if p["path"] == path]
            entry = {
                "path": path,
                "title": item.get("title", path.split("/")[-1] or path),
                "type": item.get("type", "page"),
                "modified": now if t == "api_items_added" else (item.get("modified") or now),
                "date_gmt": item.get("date_gmt") or "",
                "author": item.get("author", 0),
            }
            if existing:
                existing[0].update(entry)
            else:
                manifest["pages"].append(entry)
    elif t == "page_content_changed":
        url = c.get("url", "")
        if url:
            path = urllib.parse.urlparse(url).path
            for p in manifest["pages"]:
                if p["path"] == path:
                    p["modified"] = now
                    if c.get("author"):
                        p["author"] = c["author"]
                    break


def _sync_manifest_from_sitemap(manifest: dict, state: dict):
    sitemap_urls = state.get("sitemap", {}).get("urls", {})
    sitemap_paths = set()
    existing_paths = {p["path"] for p in manifest["pages"]}

    for url, meta in (sitemap_urls or {}).items():
        path = urllib.parse.urlparse(url).path
        sitemap_paths.add(path)
        if path not in existing_paths:
            lastmod = meta.get("lastmod") or ""
            manifest["pages"].append({
                "path": path,
                "title": path.strip("/").split("/")[-1].replace("-", " ").title(),
                "type": "page",
                "modified": lastmod,
                "date_gmt": "",
                "author": 0,
            })

    if sitemap_paths:
        manifest["pages"] = [p for p in manifest["pages"]
                             if p["path"] in sitemap_paths or p["type"] != "page"]


def _write_if_changed(path, data: dict, key: str):
    if path.is_file():
        try:
            old = json.loads(path.read_text(encoding="utf-8"))
            if old.get(key) == data.get(key):
                return
        except Exception:
            pass
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
