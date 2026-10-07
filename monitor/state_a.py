"""STATE_A pointer for the cycle classifier.

Tracks the "last stable snapshot" for each endpoint. Used to compute
the per-post transition between the previous stable state and the
current cycle's snapshot.

The pointer is stored in `state/last_stable_snapshot.json`:

    {
      "/wp-json/wp/v2/posts": {
        "ts": "2026-07-31T15:09:59.123456+00:00",
        "path": "C:\\...\\monitor_reports\\collections\\wp-json_wp_v2_posts\\20260731_150959123456_collection.json"
      },
      ...
    }

STATE_A does NOT advance on `anomalous_cycle` (the next cycle is compared
against the same STATE_A so the anomaly can be re-examined).
"""
import json
from monitor.config import STATE_DIR

STATE_A_PATH = STATE_DIR / "last_stable_snapshot.json"


def _load():
    if not STATE_A_PATH.is_file():
        return {}
    try:
        return json.loads(STATE_A_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save(data):
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    STATE_A_PATH.write_text(
        json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def get(endpoint):
    """Return the last stable snapshot pointer for an endpoint, or None.

    Returns a dict like {"ts": ..., "path": ...} or None.
    """
    return _load().get(endpoint)


def put(endpoint, ts, path, cycle_type):
    """Update the last stable snapshot pointer for an endpoint.

    On `anomalous_cycle`, STATE_A does NOT advance: the next cycle
    will be compared against the same STATE_A so the anomaly can be
    re-examined.
    """
    if cycle_type == "anomalous_cycle":
        return
    if not endpoint or not ts or not path:
        return
    data = _load()
    data[endpoint] = {"ts": ts, "path": path}
    _save(data)
