"""Feed/manifest JSON generation for GitHub Pages site.

Produces docs/data/feed.json (change log) and docs/data/manifest.json
(known pages) sorted newest-first.
"""

import json
import re
import urllib.parse
from datetime import datetime, timezone

from monitor.config import DATA_DIR
from monitor.logger import log
from monitor.api_collections import get_user_map
from monitor.noise_filter import is_noise_diff_line


def generate_site_data(state: dict, changes: list) -> bool:
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

    consolidated = _consolidate_memory_bloc_entries(feed["entries"])
    if consolidated is not feed["entries"]:
        feed["entries"] = consolidated
        log(f"Consolidated {len(feed['entries'])} feed entries via memory bloc aggregation", "FILE")

    cleaned = _filter_noise_entries(feed["entries"])
    if cleaned is not feed["entries"]:
        removed = len(feed["entries"]) - len(cleaned)
        feed["entries"] = cleaned
        log(f"Removed {removed} noise-only feed entries", "FILE")

    tz_fixed = 0
    for e in feed["entries"]:
        for field in ("timestamp", "last_timestamp", "game_date"):
            val = e.get(field)
            if val and not re.search(r'[Zz]|[+-]\d{2}:\d{2}$', val):
                e[field] = val + '+00:00'
                tz_fixed += 1
    if tz_fixed:
        log(f"Fixed timezone on {tz_fixed} feed entry timestamps", "FILE")

    manifest = {}
    if manifest_path.is_file():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception:
            manifest = {}
    manifest.setdefault("pages", [])

    for p in manifest["pages"]:
        for field in ("modified", "date_gmt"):
            val = p.get(field)
            if val and not re.search(r'[Zz]|[+-]\d{2}:\d{2}$', val):
                p[field] = val + '+00:00'
                tz_fixed += 1
    if tz_fixed:
        log(f"Fixed timezone on {tz_fixed} feed/manifest timestamps", "FILE")

    feed["entries"] = _merge_adjacent_bloc_entries(feed["entries"], 3600)

    new_entries = []
    memory_bloc_groups = {}

    unpub_lookup = {}
    for c in changes:
        if c["type"] == "unpublished_to_published":
            pid = c.get("id")
            if pid:
                first_seen = c.get("first_seen", "")
                for e in feed["entries"]:
                    if e.get("type") == "unpublished_detected" and e.get("id") == pid:
                        feed_ts = e.get("timestamp", "")
                        if feed_ts and (not first_seen or feed_ts < first_seen):
                            first_seen = feed_ts
                        break
                unpub_lookup[pid] = {
                    "endpoint": c.get("endpoint", ""),
                    "first_seen": first_seen,
                }

    # Also check state's recently_published log for items detected in a previous cycle
    probe = state.get("probe", {})
    for pid_str, info in probe.get("recently_published", {}).items():
        pid = int(pid_str)
        if pid not in unpub_lookup:
            unpub_lookup[pid] = {
                "endpoint": info.get("endpoint", ""),
                "first_seen": info.get("first_seen", ""),
            }

    # Pass 1: Augment api_items_added with unpublished context
    consumed_unpub = set()
    for c in changes:
        if c["type"] == "api_items_added":
            unpub_notes = []
            for item in c.get("items", []):
                pid = item.get("id")
                if pid in unpub_lookup:
                    info = unpub_lookup[pid]
                    ts_str = info.get("first_seen", "")
                    if ts_str:
                        try:
                            d = datetime.fromisoformat(ts_str)
                            ts_short = d.strftime("%Y-%m-%d")
                        except Exception:
                            ts_short = ts_str[:10] if ts_str else "?"
                    else:
                        ts_short = "?"
                    unpub_notes.append(f"#{pid} ({ts_short})")
                    consumed_unpub.add(pid)
            if unpub_notes:
                existing = c.get("detail", "")
                c["detail"] = f"{existing} — previously unpublished: {', '.join(unpub_notes)}"

    # Pass 2: Main processing loop
    for c in changes:
        is_memory_bloc = False
        memory_bloc_detected = False
        if c["type"] in ("page_content_changed", "api_items_added"):
            diffs = c.get("diffs", [])
            if diffs:
                raw = diffs[0].get("diff", "")
                old_val, new_val, bloc_type = _parse_memory_bloc_diff(raw)
                if new_val and old_val != new_val:
                    entry_type = 'memory_bloc_restoration' if 'restoration' in (bloc_type or '') else 'memory_bloc_verification'
                    group_key = (entry_type, new_val)
                    group = memory_bloc_groups.setdefault(group_key, {
                        "old_value": old_val, "new_value": new_val,
                        "bloc_type": entry_type,
                        "changes": [], "first_ts": None,
                    })
                    group["changes"].append(c)
                    if group["first_ts"] is None:
                        group["first_ts"] = c.get("ts") or datetime.now(timezone.utc).isoformat()
                    memory_bloc_detected = True
                    if c["type"] == "page_content_changed":
                        is_memory_bloc = True

        if not is_memory_bloc:
            ts = None
            if c["type"] == "page_content_changed":
                url = c.get("url", "")
                page_s = state.get("pages", {}).get(url, {})
                lm = page_s.get("last_modified", "")
                if lm:
                    try:
                        dt = datetime.strptime(lm, "%a, %d %b %Y %H:%M:%S %Z").replace(tzinfo=timezone.utc)
                        ts = dt.isoformat()
                    except Exception:
                        pass
                if not ts:
                    ts = c.get("ts")
            elif c["type"] == "api_items_modified":
                items = c.get("items", [])
                gmts = [i.get("modified_gmt", "") for i in items if i.get("modified_gmt")]
                if gmts:
                    try:
                        ts = max(gmts)
                    except Exception:
                        pass
            elif c["type"] == "api_items_added":
                items = c.get("items", [])
                gmts = [i.get("modified_gmt", "") for i in items if i.get("modified_gmt")]
                if gmts:
                    try:
                        ts = max(gmts)
                    except Exception:
                        pass
            elif c["type"] in ("media_orphan_upload", "media_upload"):
                mg = c.get("modified_gmt", "")
                if mg:
                    ts = mg

            if c["type"] == "unpublished_to_published":
                if c.get("id") in consumed_unpub:
                    continue

            entry = _change_to_feed_entry(c, ts)
            if entry:
                if entry["type"] == "unpublished_detected" and c.get("id"):
                    pid = c["id"]
                    if any(e.get("type") == "unpublished_detected" and e.get("id") == pid
                           for e in feed["entries"]):
                        continue
                feed["entries"].append(entry)
                _update_manifest(manifest, c)
                new_entries.append(entry)

    for group_key, group in memory_bloc_groups.items():
        bloc_type = group["bloc_type"]
        value = group["new_value"]
        timestamps = [c.get("ts") or datetime.now(timezone.utc).isoformat() for c in group["changes"]]
        last_ts = max(timestamps)
        first_ts = min(timestamps)
        existing = _find_memory_bloc_entry(feed["entries"], value, bloc_type)
        new_page_count = sum(1 for c in group["changes"] if c["type"] == "api_items_added")
        label = 'restoration' if bloc_type == 'memory_bloc_restoration' else 'verification'
        val_key = 'restoration_value' if bloc_type == 'memory_bloc_restoration' else 'verification_value'
        if existing:
            if new_page_count:
                existing["page_count"] += new_page_count
            count = existing["page_count"]
            existing["detail"] = f"Memory bloc {label} changed from {group['old_value']} to {group['new_value']} across {count} pages"
            existing["title"] = f"Memory bloc {label}: {group['new_value']} [{count} Pages]"
            existing["timestamp"] = first_ts if first_ts < existing["timestamp"] else existing["timestamp"]
            existing["last_timestamp"] = last_ts if last_ts > existing.get("last_timestamp", "") else existing.get("last_timestamp", last_ts)
            if existing.get("last_timestamp", "") > existing.get("timestamp", ""):
                existing["timestamp"] = existing["last_timestamp"]
            new_entries.append(existing)
        else:
            count = new_page_count or len(group["changes"])
            recent = _find_recent_bloc_entry(feed["entries"], last_ts, 3600, bloc_type)
            if recent and new_page_count:
                old_diff = recent.get("diff", "")
                old_old = old_diff.split("→")[0].strip() if "→" in old_diff else "?"
                old_val = old_old if old_old != "?" else group["old_value"]
                recent[val_key] = value
                recent["page_count"] = recent.get("page_count", 0) + new_page_count
                c = recent["page_count"]
                recent["title"] = f"Memory bloc {label}: {value} [{c} Pages]"
                recent["diff"] = f"{old_val} → {value}"
                recent["detail"] = f"Memory bloc {label} changed from {old_val} to {value} across {c} pages"
                if last_ts > recent.get("last_timestamp", ""):
                    recent["last_timestamp"] = last_ts
                recent["timestamp"] = recent["last_timestamp"]
                new_entries.append(recent)
            else:
                count = new_page_count or len(group["changes"])
                entry = {
                    "type": "memory_bloc_restoration",
                    "timestamp": first_ts,
                    "title": f"Memory bloc restoration: {group['new_value']} [{count} Pages]",
                    "link": group["changes"][0].get("url", ""),
                    "diff": f"{group['old_value']} → {group['new_value']}",
                    "detail": f"Memory bloc restoration changed from {group['old_value']} to {group['new_value']} across {count} pages",
                    "author": "System",
                    "site": "",
                    "restoration_value": value,
                    "page_count": count,
                    "last_timestamp": last_ts,
                }
                feed["entries"].append(entry)
                new_entries.append(entry)

    _sync_manifest_from_sitemap(manifest, state)

    feed["entries"] = feed["entries"][-2000:]

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

    feed["entries"].sort(key=lambda e: (e.get("last_timestamp") or e.get("timestamp", "")), reverse=True)
    manifest["pages"].sort(key=lambda p: p.get("modified", ""), reverse=True)

    feed["updated"] = datetime.now(timezone.utc).isoformat()
    manifest["updated"] = datetime.now(timezone.utc).isoformat()

    feed_written = _write_if_changed(feed_path, feed, "entries")
    _write_if_changed(manifest_path, manifest, "pages")

    if feed_written:
        log(f"Feed updated: {len(feed['entries'])} entries, {len(manifest['pages'])} manifest pages", "FILE")
        sync_path = DATA_DIR / "sync.json"
        sync_path.write_text(json.dumps({"timestamp": datetime.now(timezone.utc).isoformat()}, indent=2), encoding="utf-8")
        log("Sync timestamp written", "FILE")

    return feed_written


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

    data["entries"] = data["entries"][-2000:]
    data["entries"].sort(key=lambda e: e.get("timestamp", ""), reverse=True)
    data["updated"] = datetime.now(timezone.utc).isoformat()

    _write_if_changed(path, data, "entries")
    log(f"External data written: {sum(1 for e in data['entries'] if e['type'].startswith('external_'))} external entries", "FILE")


def seed_feed_from_mirror(state: dict):
    feed_path = DATA_DIR / "feed.json"
    existing_ids = set()
    media_fixup = {}

    if feed_path.is_file():
        try:
            feed = json.loads(feed_path.read_text(encoding="utf-8"))
            for e in feed.get("entries", []):
                if e.get("id") and e.get("type") in ("api_items_added", "media_orphan_upload", "media_upload"):
                    existing_ids.add(e["id"])
                    if e.get("type") in ("media_orphan_upload", "media_upload"):
                        media_fixup[e["id"]] = e
        except Exception:
            pass

    api = state.get("api", {})

    # Fix up existing media entry timestamps and types from API data
    if media_fixup:
        any_fixed = False
        for item in api.get("/wp-json/wp/v2/media", {}).get("items", []):
            item_id = item.get("id")
            if item_id in media_fixup:
                entry = media_fixup[item_id]
                mg = item.get("modified_gmt", "")
                if mg:
                    existing_ts = entry.get("timestamp", "")
                    existing_clean = re.sub(r'\..*$', '', existing_ts).replace('+00:00', '').replace('Z', '')
                    mg_clean = mg[:19] if len(mg) >= 19 else mg
                    if existing_clean != mg_clean:
                        entry["timestamp"] = mg
                        any_fixed = True
                is_orphan = not item.get("post_parent", 0)
                correct_type = "media_orphan_upload" if is_orphan else "media_upload"
                if entry.get("type") != correct_type:
                    entry["type"] = correct_type
                    any_fixed = True
        if any_fixed:
            feed["entries"] = feed.get("entries", [])
            feed["entries"].sort(key=lambda e: (e.get("last_timestamp") or e.get("timestamp", "")), reverse=True)
            _write_if_changed(feed_path, feed, "entries")
            log("Fixed media entry timestamps from API data", "FILE")

    log("Seeding feed from existing mirror data...", "FILE")
    seed_changes = []

    for ep, label in [("/wp-json/wp/v2/pages", "pages"), ("/wp-json/wp/v2/posts", "posts")]:
        for item in api.get(ep, {}).get("items", []):
            item_id = item.get("id")
            if item_id and item_id in existing_ids:
                continue
            seed_changes.append({
                "type": "api_items_added",
                "endpoint": ep,
                "detail": item.get("title", f"Untitled {label}"),
                "items": [item],
            })

    for item in api.get("/wp-json/wp/v2/media", {}).get("items", []):
        item_id = item.get("id")
        if item_id and item_id in existing_ids:
            continue
        is_orphan = not item.get("post_parent", 0)
        seed_changes.append({
            "type": "media_orphan_upload" if is_orphan else "media_upload",
            "endpoint": "media",
            "detail": item.get("title", f"Media #{item_id or '?'}"),
            "id": item_id,
            "title": item.get("title", "Untitled media"),
            "url": item.get("link", ""),
            "author": item.get("author", 0),
            "modified_gmt": item.get("modified_gmt", ""),
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


def _change_to_feed_entry(c: dict, ts: str = None) -> dict | None:
    t = c["type"]
    now = ts or datetime.now(timezone.utc).isoformat()
    # API modified_gmt lacks timezone suffix — assume UTC
    if now and not re.search(r'[Zz]|[+-]\d{2}:\d{2}$', now):
        now = now + '+00:00'
    link = ""
    title = c.get("detail", "unknown")
    author = 0
    diff = ""
    entry_id = None
    game_date = ""

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
        entry_id = items[0].get("id") if items else None
        game_date = items[0].get("date_gmt", "") if items else ""
        if game_date and not re.search(r'[Zz]|[+-]\d{2}:\d{2}$', game_date):
            game_date = game_date + '+00:00'
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
        if not diff and items:
            item = items[0]
            old_m = item.get("old_modified", "?")[:19] if item.get("old_modified") else "?"
            new_m = item.get("modified", "?")[:19]
            diff = f"- modified: {old_m}\n+ modified: {new_m}"
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
        entry_id = c.get("id")
    elif t == "media_upload":
        title = c.get("title", f"Media #{c.get('id', '?')}")
        link = c.get("url", "")
        author = c.get("author", 0)
        entry_id = c.get("id")
    elif t == "unpublished_detected":
        title = f"#{c.get('id', '?')} ({c.get('endpoint', '')})"
        link = f"https://{c.get('site', '')}/"
        diff = f"HTTP {c.get('status', '?')}"
        entry_id = c.get("id")
    elif t == "external_unpublished_to_published":
        pid = c.get("id", "?")
        ep = c.get("endpoint", "")
        site = c.get("site", c.get("hostname", ""))
        first_seen = c.get("first_seen", "")
        title = f"Previously unpublished {ep} #{pid} on {site}"
        if first_seen:
            try:
                d = datetime.fromisoformat(first_seen)
                title += f" (since {d.strftime('%Y-%m-%d')})"
            except Exception:
                title += f" (since {first_seen[:10]})"
        link = f"https://{site}/"
        diff = f"+ #{pid}: {ep} on {site} is now public"
    elif t == "external_content_changed":
        title = c.get("detail", "External content changed")
        link = c.get("url", "")
        diff = c.get("diff", "")
        if not diff:
            items = c.get("items", [])
            if items:
                lines = [f"+ #{i.get('id','?')}: {i.get('title','')[:80]}" for i in items[:20]]
                diff = "\n".join(lines)
            elif c.get("detail"):
                diff = c["detail"][:500]
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
        "id": entry_id if t in ("api_items_added", "unpublished_detected", "media_orphan_upload", "media_upload") else None,
        "game_date": game_date if t in ("api_items_added", "media_orphan_upload", "media_upload") else "",
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
    has_change_lines = any(l.startswith(("- ", "+ ")) for l in lines_out)
    if not has_change_lines:
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


_MEMORY_BLOC_RE = re.compile(r'(?:Memory_bloc_restoration|Memory_bloc_verification):\s*(\d+/\d+)')
_BLOC_TYPE_RE = re.compile(r'(Memory_bloc_restoration|Memory_bloc_verification):\s*(\d+/\d+)')


def _parse_memory_bloc_diff(diff_text: str):
    if not diff_text:
        return None, None, None
    matches = _BLOC_TYPE_RE.findall(diff_text)
    # Group matches by type (restoration vs verification), then return first
    # type that has both an old and new value
    types = {}
    for typ, val in matches:
        types.setdefault(typ, []).append(val)
    for typ, vals in types.items():
        if len(vals) >= 2:
            return vals[0], vals[1], typ
    return None, None, None


def _find_memory_bloc_entry(entries: list, value: str, bloc_type: str = 'memory_bloc_restoration'):
    value_key = 'restoration_value' if bloc_type == 'memory_bloc_restoration' else 'verification_value'
    for entry in entries:
        if entry.get("type") != bloc_type:
            continue
        if entry.get(value_key) == value:
            return entry
        if not entry.get(value_key) and value in (entry.get("title", "") or ""):
            return entry
    return None


def _find_recent_bloc_entry(entries: list, timestamp_iso: str, threshold: float = 3600, bloc_type: str = 'memory_bloc_restoration'):
    target = _parse_iso(timestamp_iso)
    if target is None:
        return None
    best = None
    best_gap = None
    for entry in entries:
        if entry.get("type") != bloc_type:
            continue
        ets = _parse_iso(entry.get("last_timestamp") or entry.get("timestamp", ""))
        if ets is None:
            continue
        gap = abs((target - ets).total_seconds())
        if gap <= threshold and (best_gap is None or gap < best_gap):
            best = entry
            best_gap = gap
    return best


def _parse_iso(iso_str: str):
    if not iso_str:
        return None
    try:
        return datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
    except Exception:
        return None


def _consolidate_memory_bloc_entries(entries: list) -> list:
    memory = []
    others = []
    for e in entries:
        if e.get("type") == "page_content_changed" and e.get("diff"):
            old, new, btype = _parse_memory_bloc_diff(e["diff"])
            if new:
                memory.append((e, btype))
                continue
        others.append(e)

    if not memory:
        return entries

    groups = {}
    for e, btype in memory:
        _, new, _ = _parse_memory_bloc_diff(e["diff"])
        if new:
            key = (btype, new)
            groups.setdefault(key, []).append(e)

    for (btype, value), items in groups.items():
        timestamps = [e["timestamp"] for e in items if e.get("timestamp")]
        oldest = min(timestamps) if timestamps else datetime.now(timezone.utc).isoformat()
        newest = max(timestamps) if timestamps else oldest
        old_val, _, _ = _parse_memory_bloc_diff(items[0].get("diff", ""))

        entry_type = 'memory_bloc_restoration' if 'restoration' in (btype or '') else 'memory_bloc_verification'
        label = 'restoration' if entry_type == 'memory_bloc_restoration' else 'verification'
        val_key = 'restoration_value' if entry_type == 'memory_bloc_restoration' else 'verification_value'

        existing = _find_memory_bloc_entry(others, value, entry_type)
        if existing:
            new_page_count = sum(1 for e in items if e.get("type") == "api_items_added")
            if new_page_count:
                existing["page_count"] = existing.get("page_count", 0) + new_page_count
            count = existing["page_count"]
            existing["title"] = f"Memory bloc {label}: {value} [{count} Pages]"
            existing["detail"] = f"Memory bloc {label} changed from {old_val} to {value} across {count} pages"
            existing["timestamp"] = oldest
            existing["last_timestamp"] = newest
        else:
            count = len(items)
            others.append({
                "type": entry_type,
                "timestamp": oldest,
                "title": f"Memory bloc {label}: {value} [{count} Pages]",
                "link": items[0].get("link", ""),
                "diff": f"{old_val} \u2192 {value}" if old_val else value,
                "detail": f"Memory bloc {label} changed from {old_val} to {value} across {count} pages",
                "author": "System",
                "site": "",
                val_key: value,
                "page_count": count,
                "last_timestamp": newest,
            })

    return others


def _filter_noise_entries(entries: list) -> list:
    filtered = []
    removed = 0
    for e in entries:
        if e.get("type") == "page_content_changed" and e.get("diff"):
            lines = e["diff"].split("\n")
            meaningful = any(
                l.startswith(("- ", "+ ")) and not is_noise_diff_line(l)
                for l in lines
            )
            if not meaningful:
                removed += 1
                continue
        filtered.append(e)
    if removed:
        log(f"Filtered out {removed} noise-only entries from feed", "FILE")
    return filtered


def _merge_adjacent_bloc_entries(entries: list, threshold: float = 3600) -> list:
    bloc_types = ('memory_bloc_restoration', 'memory_bloc_verification')
    bloc = [(i, e) for i, e in enumerate(entries) if e.get("type") in bloc_types]
    if len(bloc) < 2:
        return entries
    bloc.sort(key=lambda x: _parse_iso(x[1].get("last_timestamp") or x[1].get("timestamp", "")) or datetime.min)
    merged_indices = set()
    result = list(entries)
    for i in range(len(bloc) - 1):
        if i in merged_indices:
            continue
        ea = bloc[i][1]
        if ea.get("type") not in bloc_types:
            continue
        ea_lts = _parse_iso(ea.get("last_timestamp") or ea.get("timestamp", ""))
        ea_ts = _parse_iso(ea.get("timestamp", ""))
        if ea_lts is None:
            continue
        for j in range(i + 1, len(bloc)):
            if j in merged_indices:
                continue
            eb = bloc[j][1]
            if eb.get("type") != ea.get("type"):
                continue
            eb_lts = _parse_iso(eb.get("last_timestamp") or eb.get("timestamp", ""))
            eb_ts = _parse_iso(eb.get("timestamp", ""))
            if eb_lts is None or eb_ts is None:
                continue
            if (eb_lts - ea_lts).total_seconds() <= threshold and abs((eb_ts - ea_ts).total_seconds()) <= threshold:
                label = 'restoration' if ea.get('type') == 'memory_bloc_restoration' else 'verification'
                val_key = 'restoration_value' if ea.get('type') == 'memory_bloc_restoration' else 'verification_value'
                ea_old = ea.get("diff", "").split("\u2192")[0].strip() if "\u2192" in ea.get("diff", "") else "?"
                eb_val = eb.get(val_key, "")
                if eb_val and int(eb_val.split("/")[0]) > int(ea.get(val_key, "0").split("/")[0]):
                    ea[val_key] = eb_val
                ea["page_count"] = ea.get("page_count", 0) + eb.get("page_count", 0)
                ea["title"] = f"Memory bloc {label}: {ea[val_key]} [{ea['page_count']} Pages]"
                ea["diff"] = f"{ea_old} \u2192 {ea[val_key]}"
                ea["detail"] = f"Memory bloc {label} changed from {ea_old} to {ea[val_key]} across {ea['page_count']} pages"
                if eb.get("last_timestamp", "") > ea.get("last_timestamp", ""):
                    ea["last_timestamp"] = eb["last_timestamp"]
                ea["timestamp"] = ea["last_timestamp"]
                ea_lts = _parse_iso(ea.get("last_timestamp") or ea.get("timestamp", ""))
                ea_ts = _parse_iso(ea.get("timestamp", ""))
                merged_indices.add(j)
                result[bloc[i][0]] = ea
                result[bloc[j][0]] = None
            else:
                break
    result = [e for e in result if e is not None]
    if len(result) != len(entries):
        log(f"Merged {len(entries) - len(result)} adjacent memory bloc entries (threshold={threshold}s)", "FILE")
    return result


def _write_if_changed(path, data: dict, key: str) -> bool:
    if path.is_file():
        try:
            old = json.loads(path.read_text(encoding="utf-8"))
            if old.get(key) == data.get(key):
                return False
        except Exception:
            pass
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    return True
