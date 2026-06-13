"""Unpublished ID probing -- chunked sweep for hidden/draft content."""

from datetime import datetime, timezone

from monitor.config import BASE_URL, PROBE_RANGE, PROBE_CHUNK_SIZE
from monitor.http_client import head_url, jitter
from monitor.logger import log


def _entry_id(entry):
    if isinstance(entry, dict):
        return entry.get("id", 0)
    if isinstance(entry, (list, tuple)) and len(entry) > 0:
        return entry[0]
    return 0


def _migrate_log(ulog):
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
        log("Migrated unpublished log entries to dict format", "FILE")


def probe_unpublished(state: dict) -> list:
    changes = []
    max_id = _get_max_known_id(state)
    if max_id == 0:
        return changes

    probe_state = state.setdefault("probe", {})
    probe_pos = probe_state.get("position", max_id + 1)
    probe_ceiling = max_id + PROBE_RANGE

    if probe_pos > probe_ceiling:
        probe_pos = max_id + 1

    chunk_end = min(probe_pos + PROBE_CHUNK_SIZE - 1, probe_ceiling)

    unpublished_log = probe_state.setdefault("unpublished", {"posts": [], "pages": []})
    _migrate_log(unpublished_log)

    for pid in range(probe_pos, chunk_end + 1):
        for ep_template in ["/wp-json/wp/v2/posts/{id}", "/wp-json/wp/v2/pages/{id}"]:
            url = f"{BASE_URL}{ep_template.replace('{id}', str(pid))}"
            result = head_url(url)
            if result.status in (401, 403):
                ep_name = "posts" if "/posts/" in url else "pages"
                changes.append({
                    "type": "unpublished_detected",
                    "id": pid,
                    "status": result.status,
                    "endpoint": ep_name,
                    "detail": f"Unpublished {ep_name} #{pid} (HTTP {result.status})",
                })
                seen_ids = {_entry_id(e) for e in unpublished_log[ep_name]}
                if pid not in seen_ids:
                    unpublished_log[ep_name].append({
                        "id": pid,
                        "first_seen": datetime.now(timezone.utc).isoformat(),
                    })
                log(f"Unpublished {ep_name} #{pid} (HTTP {result.status})", "DEEP")
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
                first_seen = ""
                if isinstance(found_entry, dict):
                    first_seen = found_entry.get("first_seen", "")
                changes.append({
                    "type": "unpublished_to_published",
                    "id": pid,
                    "endpoint": ep_name,
                    "first_seen": first_seen,
                    "detail": f"Previously unpublished {ep_name} #{pid} is now public",
                })
                log(f"Newly published {ep_name} #{pid} (was hidden)", "DEEP")
        jitter(0.08, 0.1)

    probe_state["position"] = chunk_end + 1
    probe_state["last_probed"] = datetime.now(timezone.utc).isoformat()

    log(f"Probe: checked IDs {probe_pos}-{chunk_end} (next: {chunk_end + 1}, ceiling: {probe_ceiling})", "DEEP")

    return changes


def _get_max_known_id(state: dict) -> int:
    max_id = 0
    for ep in ["/wp-json/wp/v2/posts", "/wp-json/wp/v2/pages", "/wp-json/wp/v2/media"]:
        api_state = state.get("api", {}).get(ep, {})
        items = api_state.get("items", [])
        for item in items:
            iid = item.get("id", 0)
            if iid > max_id:
                max_id = iid
    return max_id
