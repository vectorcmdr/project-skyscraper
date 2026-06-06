"""Unpublished ID probing -- chunked sweep for hidden/draft content."""

from datetime import datetime, timezone

from monitor.config import BASE_URL, PROBE_RANGE, PROBE_CHUNK_SIZE
from monitor.http_client import head_url, jitter
from monitor.logger import log


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
                log(f"Unpublished {ep_name} #{pid} (HTTP {result.status})", "DEEP")
            elif result.status == 200:
                ep_name = "posts" if "/posts/" in url else "pages"
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
