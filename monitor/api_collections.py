"""WP REST API collection checking -- paginated fetch and item-level diffing."""

import hashlib
import json
from datetime import datetime, timezone

from monitor.config import BASE_URL, COLLECTION_ENDPOINTS
from monitor.http_client import fetch, jitter
from monitor.logger import log


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

    if added_ids:
        added_details = [new_items_map[iid] for iid in sorted(added_ids)]
        changes.append({
            "type": "api_items_added",
            "endpoint": endpoint,
            "count": len(added_ids),
            "items": added_details[:30],
            "detail": f"{len(added_ids)} new item(s) in {endpoint}",
        })
        log(f"API {endpoint}: +{len(added_ids)} new items: "
            f"{', '.join(str(n['id']) for n in added_details[:10])}", "MEDIUM")

    if removed_ids:
        changes.append({
            "type": "api_items_removed",
            "endpoint": endpoint,
            "count": len(removed_ids),
            "ids": sorted(removed_ids, key=int)[:30],
            "detail": f"{len(removed_ids)} item(s) removed from {endpoint}",
        })
        log(f"API {endpoint}: -{len(removed_ids)} items removed", "MEDIUM")

    if changed_items:
        changes.append({
            "type": "api_items_modified",
            "endpoint": endpoint,
            "count": len(changed_items),
            "items": [
                {
                    "id": c[2]["id"],
                    "title": c[2]["title"],
                    "link": c[2].get("link", ""),
                    "type": c[2].get("type", ""),
                    "modified": c[2].get("modified", ""),
                    "date_gmt": c[2].get("date_gmt", ""),
                    "author": c[2].get("author", 0),
                    "old_modified": c[1].get("modified", ""),
                    "new_modified": c[2]["modified"],
                }
                for c in changed_items[:30]
            ],
            "detail": f"{len(changed_items)} item(s) modified in {endpoint}",
        })
        log(f"API {endpoint}: ~{len(changed_items)} modified items", "MEDIUM")

    if not added_ids and not removed_ids and not changed_items:
        log(f"API {endpoint}: hash changed but no item diff (meta only)", "MEDIUM")

    api_state["etag"] = new_etag or result.etag
    api_state["last_modified"] = new_last_modified or result.last_modified
    api_state["hash"] = new_hash
    api_state["last_checked"] = datetime.now(timezone.utc).isoformat()
    api_state["items"] = [new_items_map[iid] for iid in sorted(new_items_map, key=int)]
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
        pr = fetch(page_url)
        if pr.ok and pr.content:
            all_raw += pr.content
            try:
                page_items = json.loads(pr.text)
                if isinstance(page_items, list):
                    all_items.extend(page_items)
            except json.JSONDecodeError:
                pass

    combined_hash = hashlib.md5(all_raw).hexdigest()
    return all_items, combined_hash, total_pages, final_etag, final_last_modified


def _item_summary(item: dict, endpoint: str = "") -> dict:
    raw_cats = item.get("categories")
    raw_tags = item.get("tags")
    result = {
        "id": item["id"],
        "title": item.get("title", {}).get("rendered", "") if isinstance(item.get("title"), dict) else "",
        "modified": item.get("modified", ""),
        "type": item.get("type", ""),
        "status": item.get("status", ""),
        "link": item.get("link", ""),
        "author": item.get("author", 0),
        "name": item.get("name", ""),
        "date_gmt": item.get("date_gmt", ""),
        "post_parent": item.get("post_parent", 0) or 0,
        "parent": item.get("parent", 0) or 0,
        "categories": list(raw_cats) if isinstance(raw_cats, list) else [],
        "tags": list(raw_tags) if isinstance(raw_tags, list) else [],
    }
    if item.get("type") in ("wp_navigation", "wp_block", "nav_menu_item"):
        raw = item.get("content", {})
        if isinstance(raw, dict):
            result["content_rendered"] = raw.get("rendered", "")
    if "/categories" in endpoint or "/tags" in endpoint or "/users" in endpoint:
        result["count"] = item.get("count", 0)
    return result


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
