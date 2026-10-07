"""Roll-up stats and classification log for memory-bloc wave quiescence.

Two files are managed here:

1. `state/wave_stats.json` -- small JSON counter for the periodic roll-up
   cadence (every WAVE_ROLLUP_N_CYCLES normal cycles, or every
   WAVE_ROLLUP_HOURS hours since the last roll-up, whichever first).

2. `state/wave_classification.jsonl` -- append-only JSON Lines log of
   every cycle's classification. The audit trail for the cycle
   classifier. Operators can grep this file to verify the classifier
   is working as expected (e.g. `grep '"cycle_type": "anomalous_cycle"'`).
"""
import json
from datetime import datetime, timezone

from monitor.config import STATE_DIR, WAVE_ROLLUP_HOURS, WAVE_ROLLUP_N_CYCLES

WAVE_STATS_PATH = STATE_DIR / "wave_stats.json"
CLASSIFICATION_LOG_PATH = STATE_DIR / "wave_classification.jsonl"


def get_stats():
    if not WAVE_STATS_PATH.is_file():
        return {"last_summary_ts": None, "normal_cycles_since_summary": 0}
    try:
        return json.loads(WAVE_STATS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {"last_summary_ts": None, "normal_cycles_since_summary": 0}


def save_stats(stats):
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    WAVE_STATS_PATH.write_text(
        json.dumps(stats, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def record_normal_cycle():
    """Increment the normal cycle counter and return the updated stats."""
    stats = get_stats()
    stats["normal_cycles_since_summary"] = (
        stats.get("normal_cycles_since_summary", 0) + 1
    )
    save_stats(stats)
    return stats


def should_emit_rollup():
    """Check whether a roll-up commit should fire.

    True when either:
    - normal_cycles_since_summary >= WAVE_ROLLUP_N_CYCLES, OR
    - the last roll-up was >= WAVE_ROLLUP_HOURS hours ago
    """
    stats = get_stats()
    n = stats.get("normal_cycles_since_summary", 0)
    if n >= WAVE_ROLLUP_N_CYCLES:
        return True
    last = stats.get("last_summary_ts")
    if last:
        try:
            last_dt = datetime.fromisoformat(last)
            elapsed = (
                datetime.now(timezone.utc) - last_dt
            ).total_seconds()
            if elapsed >= WAVE_ROLLUP_HOURS * 3600:
                return True
        except Exception:
            pass
    return False


def reset_after_rollup():
    """Reset the roll-up counter and record the current timestamp."""
    stats = get_stats()
    stats["normal_cycles_since_summary"] = 0
    stats["last_summary_ts"] = datetime.now(timezone.utc).isoformat()
    save_stats(stats)
    return stats


def log_classification(change):
    """Append one line to the classification log for a cycle.

    Called once per api_items_modified change after cycle_classifier has
    attached `cycle_type` and `breaking_post_ids` to the change dict.

    The log is the audit trail for the classifier. Grep it to verify:
      grep '"cycle_type": "anomalous_cycle"' state/wave_classification.jsonl
      grep '"silent": false' state/wave_classification.jsonl
    """
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    endpoint = change.get("endpoint", "")
    cycle_type = change.get("cycle_type", "")
    items = change.get("items", [])
    breaking = change.get("breaking_post_ids", [])
    silent = cycle_type in ("trivial", "normal_round_trip", "normal_dribble")
    ts = change.get("ts") or datetime.now(timezone.utc).isoformat()
    line = {
        "ts": ts,
        "endpoint": endpoint,
        "cycle_type": cycle_type,
        "item_count": len(items) if isinstance(items, list) else 0,
        "breaking_post_ids": breaking,
        "silent": silent,
    }
    with CLASSIFICATION_LOG_PATH.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(line, ensure_ascii=False) + "\n")
