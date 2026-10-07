"""WP REST API collection checking -- paginated fetch and item-level diffing."""

import hashlib
import html
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from monitor.config import BASE_URL, COLLECTION_ENDPOINTS, COLLECTION_SNAPSHOT_DIR, SNAPSHOT_KEEP
from monitor.http_client import fetch, jitter
from monitor.logger import log
from monitor.cycle_classifier import classify_cycle, load_snapshot_items
from monitor.state_a import get as state_a_get
from monitor.noise_filter import strip_page_noise


def check_api_collection(endpoint: str, state: dict) -> list:
    changes = []
    url = f"{BASE_URL}{endpoint}"
    api_state = state.setdefault("api", {}).setdefault(endpoint, {})

    etag = api_state.get("etag")
    last_modified = api_state.get("last_modified")

    result = fetch(url, etag=etag, last_modified=last_modified)

    if result.not_modified:
        log(f"API {endpoint}: unchanged (304)", "MEDIUM")
        api_state["last_checked"] = datetime.now(timezone.utc).isoformat()
        return changes

    items, new_hash, total_pages, new_etag, new_last_modified = _fetch_all_pages(url)

    if not items:
        log(f"API {endpoint}: fetch failed or empty", "WARN")
        return changes

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

    log(f"API {endpoint}: {len(new_ids)} items across {total_pages} page(s)", "DEBUG")

    added_ids = new_ids - known_ids

    removed_ids = known_ids - new_ids
    changed_items = []

    for iid in new_ids & known_ids:
        new_item = new_items_map[iid]
        old_item = known_items.get(iid, {})
        if new_item.get("modified") and old_item.get("modified") and new_item["modified"] != old_item["modified"]:
            changed_items.append((iid, old_item, new_item))
        elif new_item.get("modified") and not old_item.get("modified"):
            changed_items.append((iid, old_item, new_item))
        elif (new_item.get("content_hash") and old_item.get("content_hash")
              and new_item["content_hash"] != old_item["content_hash"]):
            changed_items.append((iid, old_item, new_item))

    cache_blip_ids = {
        iid for iid, old_item, new_item in changed_items
        if _is_cache_blip_change(endpoint, iid, old_item, new_item)
    }
    if cache_blip_ids:
        log(f"API {endpoint}: cache-staleness blip(s) detected: "
            f"{', '.join(sorted(cache_blip_ids, key=int))}", "MEDIUM")

    snapshot_path = None
    prev_snapshot_path = None
    if changed_items or added_ids or removed_ids:
        snapshot_path = _save_collection_snapshot(endpoint, items)
        if snapshot_path:
            prev_snapshot_path = _find_prev_snapshot(endpoint, snapshot_path)
            log(f"API {endpoint}: collection snapshot saved ({Path(snapshot_path).name})", "DEBUG")

    if added_ids:
        added_details = [new_items_map[iid] for iid in sorted(added_ids)]
        changes.append({
            "type": "api_items_added",
            "endpoint": endpoint,
            "count": len(added_ids),
            "items": added_details,
            "detail": f"{len(added_ids)} new item(s) in {endpoint}",
        })
        log(f"API {endpoint}: +{len(added_ids)} new items: "
            f"{', '.join(str(n['id']) for n in added_details[:10])}", "MEDIUM")

    # --- Removal confirmation (two-strike) ---
    # WordPress.com's API can transiently omit items from a collection for
    # a single cycle (distributed cache/index inconsistency -- e.g. the
    # posts API once returned 497/500 items, and the search index flickers
    # entries during rebuilds). A removal is only reported when the same
    # ids are observed missing on TWO CONSECUTIVE cycles. Pending items
    # stay in the known list, so their return produces no add/remove
    # notification pair.
    pending_removed = set(api_state.get("_pending_removed", []) or [])
    confirmed_removed = removed_ids & pending_removed
    kept_ids = removed_ids - confirmed_removed

    if confirmed_removed:
        removed_ids_sorted = sorted(confirmed_removed, key=int)
        removed_details = [known_items[iid] for iid in removed_ids_sorted if iid in known_items]
        changes.append({
            "type": "api_items_removed",
            "endpoint": endpoint,
            "count": len(confirmed_removed),
            "ids": removed_ids_sorted,
            "items": removed_details,
            "detail": f"{len(confirmed_removed)} item(s) removed from {endpoint}",
        })
        log(f"API {endpoint}: -{len(confirmed_removed)} items removed (confirmed)", "MEDIUM")

    if kept_ids:
        api_state["_pending_removed"] = sorted(kept_ids, key=int)
        log(f"API {endpoint}: {len(kept_ids)} removal candidate(s) pending confirmation", "MEDIUM")
    else:
        if pending_removed and not confirmed_removed:
            log(f"API {endpoint}: transient absence resolved (no notification)", "MEDIUM")
        api_state["_pending_removed"] = []

    if changed_items:
        content_only_count = sum(
            1 for c in changed_items
            if c[1].get("content_hash") and c[2].get("content_hash")
            and c[1].get("modified") == c[2].get("modified")
        )
        changes.append({
            "type": "api_items_modified",
            "endpoint": endpoint,
            "count": len(changed_items),
            "content_changes": content_only_count,
            "ts": datetime.now(timezone.utc).isoformat(),
            "snapshot": str(snapshot_path) if snapshot_path else None,
            "snapshot_prev": str(prev_snapshot_path) if prev_snapshot_path else None,
            "items": [
                {
                    "id": c[2]["id"],
                    "title": c[2]["title"],
                    "link": c[2].get("link", ""),
                    "type": c[2].get("type", ""),
                    "modified": c[2].get("modified", ""),
                    "modified_gmt": c[2].get("modified_gmt", ""),
                    "date_gmt": c[2].get("date_gmt", ""),
                    "author": c[2].get("author", 0),
                    "old_modified": c[1].get("modified", ""),
                    "new_modified": c[2]["modified"],
                    "cache_blip": str(c[2]["id"]) in cache_blip_ids,
                    "content_changed": (
                        c[1].get("content_hash") and c[2].get("content_hash")
                        and c[1].get("modified") == c[2].get("modified")
                    ),
                }
                for c in changed_items
            ],
            "detail": f"{len(changed_items)} item(s) modified in {endpoint}",
        })
        label = f"~{len(changed_items)} modified"
        if content_only_count:
            label += f" ({content_only_count} content-only)"
        log(f"API {endpoint}: {label} items", "MEDIUM")

        # Attach cycle_type and breaking_post_ids to the change dict
        # by comparing the previous stable snapshot (STATE_A) against
        # the current snapshot. On the first cycle after daemon restart
        # (no STATE_A yet), the classifier defaults to
        # page_change_with_pattern (safe default that still surfaces).
        prev_pointer = state_a_get(endpoint)
        prev_items = (
            load_snapshot_items(prev_pointer["path"])
            if prev_pointer else None
        )
        cur_items = load_snapshot_items(str(snapshot_path)) if snapshot_path else {}
        item_ids = [it["id"] for it in changes[-1]["items"]]
        cycle_type, breaking_ids = classify_cycle(prev_items, cur_items, item_ids)
        changes[-1]["cycle_type"] = cycle_type
        changes[-1]["breaking_post_ids"] = breaking_ids
        log(
            f"API {endpoint}: cycle_type={cycle_type} breaking={len(breaking_ids)}",
            "DEBUG",
        )

    if not added_ids and not removed_ids and not changed_items:
        log(f"API {endpoint}: hash changed but no item diff (meta only)", "MEDIUM")

    api_state["etag"] = new_etag or result.etag
    api_state["last_modified"] = new_last_modified or result.last_modified
    api_state["hash"] = new_hash
    api_state["last_checked"] = datetime.now(timezone.utc).isoformat()
    # Preserve unconfirmed removal candidates in the known list so their
    # return on a later cycle does not fire an api_items_added pair.
    preserved = {iid: known_items[iid] for iid in kept_ids if iid in known_items}
    api_state["items"] = (
        [new_items_map[iid] for iid in sorted(new_items_map, key=int)]
        + [preserved[iid] for iid in sorted(preserved, key=int)]
    )
    api_state["total_pages"] = total_pages

    return changes


def _fetch_all_pages(base_url: str, per_page: int = 100) -> tuple:
    all_items = []
    page = 1
    total_pages = 1
    final_etag = None
    final_last_modified = None
    all_raw = b""

    url = f"{base_url}?per_page={per_page}"
    result = fetch(url)
    if result.failed:
        return [], "", 0, None, None
    if result.content:
        all_raw = result.content

    final_etag = result.etag
    final_last_modified = result.last_modified

    try:
        items = json.loads(result.text)
    except json.JSONDecodeError:
        return [], "", 0, None, None

    if not isinstance(items, list):
        return [], "", 0, None, None

    all_items.extend(items)

    try:
        total_pages = int(result.headers.get("X-WP-TotalPages", 1))
    except (ValueError, TypeError):
        total_pages = 1

    for page in range(2, total_pages + 1):
        page_url = f"{base_url}?per_page={per_page}&page={page}"
        jitter(0.15, 0.1)
        pr = fetch(page_url, timeout=30)
        if pr.ok and pr.content:
            all_raw += pr.content
            try:
                page_items = json.loads(pr.text)
                if isinstance(page_items, list):
                    all_items.extend(page_items)
            except json.JSONDecodeError:
                pass
        else:
            log(f"API page {page}/{total_pages} fetch failed ({pr.status}), retrying...", "WARN")
            jitter(1.0, 0.5)
            pr = fetch(page_url, timeout=30)
            if pr.ok and pr.content:
                all_raw += pr.content
                try:
                    page_items = json.loads(pr.text)
                    if isinstance(page_items, list):
                        all_items.extend(page_items)
                except json.JSONDecodeError:
                    pass
            else:
                log(f"API page {page}/{total_pages} fetch failed again ({pr.status}), returning empty to avoid false changes", "WARN")
                return [], "", 0, None, None

    combined_hash = hashlib.md5(all_raw).hexdigest()
    return all_items, combined_hash, total_pages, final_etag, final_last_modified


def _item_summary(item: dict, endpoint: str = "") -> dict:
    raw_cats = item.get("categories")
    raw_tags = item.get("tags")
    result = {
        "id": item["id"],
        "title": item.get("title", {}).get("rendered", "") if isinstance(item.get("title"), dict) else "",
        "modified": item.get("modified", ""),
        "modified_gmt": item.get("modified_gmt", ""),
        "type": item.get("type", ""),
        "status": item.get("status", ""),
        "link": item.get("guid", {}).get("rendered", "") if item.get("type") == "attachment" else item.get("link", ""),
        "author": item.get("author", 0),
        "name": item.get("name", ""),
        "date_gmt": item.get("date_gmt", ""),
        "post_parent": item.get("post_parent", 0) or 0,
        "parent": item.get("parent", 0) or 0,
        "slug": item.get("slug", ""),
        "categories": list(raw_cats) if isinstance(raw_cats, list) else [],
        "tags": list(raw_tags) if isinstance(raw_tags, list) else [],
    }
    if item.get("type") in ("wp_navigation", "wp_block", "nav_menu_item"):
        raw = item.get("content", {})
        if isinstance(raw, dict):
            result["content_rendered"] = raw.get("rendered", "")
    content_text = ""
    raw_content = item.get("content")
    if isinstance(raw_content, dict):
        content_text = raw_content.get("rendered", "") or raw_content.get("raw", "")
    if content_text:
        result["content_hash"] = hashlib.md5(
            strip_page_noise(content_text).encode("utf-8")
        ).hexdigest()
    if "/categories" in endpoint or "/tags" in endpoint or "/users" in endpoint:
        result["count"] = item.get("count", 0)
    if "/comments" in endpoint:
        result["author_name"] = item.get("author_name", "")
        raw_comment = item.get("content", {})
        if isinstance(raw_comment, dict):
            rendered = raw_comment.get("rendered", "") or raw_comment.get("raw", "")
            result["comment"] = _plain_text(rendered)
    if "/media" in endpoint:
        result["source_url"] = item.get("source_url", "")
    return result


def _plain_text(raw: str, limit: int = 500) -> str:
    """Strip tags/scripts/styles, unescape entities, collapse whitespace."""
    if not raw:
        return ""
    text = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", raw)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > limit:
        text = text[:limit].rstrip() + "..."
    return text


def _snapshot_dir(endpoint: str) -> Path:
    safe = endpoint.strip("/").replace("/", "_") or "root"
    return COLLECTION_SNAPSHOT_DIR / safe


def _save_collection_snapshot(endpoint: str, items: list) -> Path | None:
    """Persist the full raw collection response when items changed.

    This captures the complete detection-time state (including content.rendered
    with memory bloc values) so bulk content-only waves can be diffed offline
    even if the live site reverts before per-item fetches run.
    """
    try:
        d = _snapshot_dir(endpoint)
        d.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(timezone.utc)
        path = d / f"{ts.strftime('%Y%m%d_%H%M%S%f')}_collection.json"
        payload = {
            "timestamp": ts.isoformat(),
            "endpoint": endpoint,
            "items": items,
        }
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        _prune_snapshots(d)
        return path
    except Exception as e:
        log(f"API {endpoint}: snapshot save failed: {e}", "WARN")
        return None


def _find_prev_snapshot(endpoint: str, current: Path) -> Path | None:
    """Find the newest snapshot for this endpoint older than `current`."""
    d = _snapshot_dir(endpoint)
    try:
        snaps = sorted(
            (p for p in d.glob("*_collection.json") if p != current),
            key=lambda p: p.name,
        )
        return snaps[-1] if snaps else None
    except Exception:
        return None


def _prune_snapshots(d: Path):
    snaps = sorted(d.glob("*_collection.json"), key=lambda p: p.name)
    for old in snaps[:-SNAPSHOT_KEEP]:
        try:
            old.unlink()
        except OSError:
            pass


def _modified_seen_in_history(endpoint: str, item_id: str, modified: str) -> bool:
    """True when a value for `modified` was previously established for this
    item in the retained collection snapshots (newest first).

    Genuine WordPress saves only ever move `modified` forward to a fresh
    wall-clock value, so a timestamp that reappears from history means the
    API served a cached/stale revision (the cache recovered) rather than a
    real new edit.
    """
    d = _snapshot_dir(endpoint)
    try:
        snaps = sorted(d.glob("*_collection.json"), key=lambda p: p.name, reverse=True)
    except Exception:
        return False
    for p in snaps:
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        for it in data.get("items", []):
            if str(it.get("id")) == str(item_id) and it.get("modified") == modified:
                return True
    return False


def _is_cache_blip_change(endpoint: str, iid: str, old_item: dict, new_item: dict) -> bool:
    """Detect a stale-cache 'blip' on a changed item.

    Two signals, both impossible for a genuine WordPress save:
      1. `modified` jumps backward in time (cache served an older revision).
      2. `modified` jumps forward but returns to a value that was already
         established in snapshot history (cache recovered to baseline).

    Content-only memory-bloc waves keep `modified` unchanged (old == new),
    so they are never classified as blips here.
    """
    old_m = old_item.get("modified")
    new_m = new_item.get("modified")
    if not old_m or not new_m or old_m == new_m:
        return False
    try:
        old_dt = datetime.fromisoformat(old_m.replace("Z", "+00:00"))
        new_dt = datetime.fromisoformat(new_m.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return False
    if new_dt < old_dt:
        return True
    if new_dt > old_dt and _modified_seen_in_history(endpoint, iid, new_m):
        return True
    return False


def get_user_map(state: dict) -> dict:
    users = state.get("api", {}).get("/wp-json/wp/v2/users", {}).get("items", [])
    result = {}
    for u in users:
        uid = u.get("id", 0)
        name = u.get("name") or u.get("title") or str(uid)
        result[uid] = name
    return result


def find_author_for_url(state: dict, url: str) -> int:
    if not url:
        return 0
    api_data = state.get("api", {})
    for ep_state in api_data.values():
        items = ep_state.get("items", [])
        if not isinstance(items, list):
            continue
        for item in items:
            if item.get("link") == url:
                return item.get("author", 0) or 0
    return 0


def find_modified_gmt_for_url(state: dict, url: str) -> str | None:
    if not url:
        return None
    api_data = state.get("api", {})
    for ep_state in api_data.values():
        items = ep_state.get("items", [])
        if not isinstance(items, list):
            continue
        for item in items:
            if item.get("link") == url:
                mg = item.get("modified_gmt")
                return mg if mg else None
    return None
