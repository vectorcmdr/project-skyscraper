"""Trace checker -- Discourse forum "The Architect" online status."""

import json
import time
from datetime import datetime, timezone

from monitor.http_client import fetch
from monitor.logger import log
from monitor.config import TRACE_DISCOURSE_URL, TRACE_STATUS_FILE, TRACE_ACTIVE_THRESHOLD, TRACE_POLL_INTERVAL

_trace_last_state = None
_trace_last_seen = None
_trace_last_poll = 0


def init_trace_state():
    global _trace_last_state, _trace_last_seen
    if TRACE_STATUS_FILE.is_file():
        try:
            data = json.loads(TRACE_STATUS_FILE.read_text(encoding="utf-8"))
            _trace_last_state = data.get("state")
            _trace_last_seen = data.get("lastSeenAt")
        except Exception:
            pass


def ensure_trace_default():
    if not TRACE_STATUS_FILE.is_file():
        TRACE_STATUS_FILE.parent.mkdir(parents=True, exist_ok=True)
        TRACE_STATUS_FILE.write_text(json.dumps({
            "state": "LOST",
            "lastSeenAt": "",
            "updatedAt": datetime.now(timezone.utc).isoformat(),
        }, indent=2), encoding="utf-8")
        log("Trace: wrote default trace.json", "FILE")


def check_trace() -> bool:
    global _trace_last_state, _trace_last_seen, _trace_last_poll

    try:
        now_ts = time.time()
        if now_ts - _trace_last_poll < TRACE_POLL_INTERVAL:
            return False
        _trace_last_poll = now_ts

        result = fetch(TRACE_DISCOURSE_URL)
        if result.failed:
            log(f"Trace fetch failed: {result.error}", "WARN")
            return False

        try:
            data = json.loads(result.text)
            user = data.get("user", {})
            last_seen_at = user.get("last_seen_at", "")
        except (json.JSONDecodeError, KeyError, TypeError) as e:
            log(f"Trace: failed to parse user data ({e})", "WARN")
            return False

        if not last_seen_at:
            return False

        now = datetime.now(timezone.utc)
        last_seen = datetime.fromisoformat(last_seen_at.replace("Z", "+00:00"))
        elapsed = (now - last_seen).total_seconds()
        new_state = "ACTIVE" if elapsed < TRACE_ACTIVE_THRESHOLD else "LOST"

        if new_state == _trace_last_state:
            _trace_last_seen = last_seen_at
            trace_data = {
                "state": new_state,
                "lastSeenAt": last_seen_at,
                "updatedAt": now.isoformat(),
            }
            TRACE_STATUS_FILE.parent.mkdir(parents=True, exist_ok=True)
            TRACE_STATUS_FILE.write_text(json.dumps(trace_data, indent=2), encoding="utf-8")
            return False

        _trace_last_state = new_state
        _trace_last_seen = last_seen_at

        trace_data = {
            "state": new_state,
            "lastSeenAt": last_seen_at,
            "updatedAt": now.isoformat(),
        }
        TRACE_STATUS_FILE.parent.mkdir(parents=True, exist_ok=True)
        TRACE_STATUS_FILE.write_text(json.dumps(trace_data, indent=2), encoding="utf-8")

        log(f"Trace state changed: {new_state} (last seen: {last_seen_at})", "INFO")
        return True

    except BaseException:
        return False
