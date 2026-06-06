"""Media checking -- file replacement, orphan uploads, thumbnail changes."""

import time

from monitor.config import BASE_URL
from monitor.http_client import head_url, fetch
from monitor.logger import log


def check_media(state: dict) -> list:
    changes = []
    media_endpoint = "/wp-json/wp/v2/media"
    api_state = state.setdefault("api", {}).setdefault(media_endpoint, {})
    known_items = {}
    if isinstance(api_state.get("items"), list):
        for i in api_state["items"]:
            known_items[str(i["id"])] = i

    thumb_state = state.setdefault("media_thumbnails", {})

    items_list = api_state.get("items", [])
    if not items_list:
        return changes

    for item in items_list:
        iid = str(item.get("id"))
        old = known_items.get(iid, {})

        old_url = old.get("source_url") if isinstance(old, dict) else ""
        new_url = item.get("source_url", "")
        if old_url and new_url and old_url != new_url:
            changes.append({
                "type": "media_replaced",
                "id": item["id"],
                "old_url": old_url,
                "new_url": new_url,
                "detail": f"Media #{item['id']} file replaced",
            })
            log(f"Media #{item['id']} file replaced: {old_url} -> {new_url}", "DEEP")

        if item.get("post_parent") == 0 and not old:
            changes.append({
                "type": "media_orphan_upload",
                "id": item["id"],
                "title": item.get("title", ""),
                "url": item.get("source_url"),
                "author": item.get("author", 0),
                "detail": f"New unattached media #{item['id']} uploaded",
            })
            log(f"Orphan media #{item['id']}", "DEEP")

        media_details = item.get("media_details", {})
        sizes = media_details.get("sizes", {}) if isinstance(media_details, dict) else {}
        known_thumbs = thumb_state.get(iid, {})
        current_thumbs = {}

        for size_name, size_info in sizes.items():
            if isinstance(size_info, dict):
                src_url = size_info.get("source_url", "")
                if src_url:
                    thumb_entry = known_thumbs.get(size_name, {})
                    if not isinstance(thumb_entry, dict):
                        thumb_entry = {}
                    old_etag = thumb_entry.get("etag")
                    result = head_url(src_url)
                    if result.ok:
                        new_etag = result.etag or result.hash
                        current_thumbs[size_name] = {"url": src_url, "etag": new_etag}
                        if old_etag and old_etag != new_etag:
                            changes.append({
                                "type": "media_thumbnail_changed",
                                "id": item["id"],
                                "media_title": item.get("title", ""),
                                "size": size_name,
                                "url": src_url,
                                "detail": f"Media #{item['id']} thumbnail '{size_name}' changed",
                            })
                            log(f"Media #{item['id']} thumbnail '{size_name}' changed", "DEEP")
                    time.sleep(0.05)

        if current_thumbs:
            thumb_state[iid] = current_thumbs

    return changes
