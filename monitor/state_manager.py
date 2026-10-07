"""Persistent JSON state management with atomic writes."""

import json
import os
import time
from datetime import datetime, timezone

from monitor.config import STATE_FILE, STATE_DIR, LOCK_FILE
from monitor.logger import log

DEFAULT_STATE = {
    "version": 3,
    "sitemap": {
        "etag": None,
        "last_modified": None,
        "hash": None,
        "last_checked": None,
        "urls": {},
        "_page_check_offset": 0,
    },
    "api": {},
    "pages": {},
    "media_thumbnails": {},
    "probe": {},
    "stats": {
        "total_checks": 0,
        "total_changes_detected": 0,
        "first_run": None,
        "last_run": None,
    },
}


def load_state() -> dict:
    if STATE_FILE.is_file():
        try:
            raw = STATE_FILE.read_text(encoding="utf-8")
            state = json.loads(raw)
            for k, v in DEFAULT_STATE.items():
                state.setdefault(k, v)
            return state
        except (json.JSONDecodeError, OSError) as e:
            log(f"State file corrupt ({e}), resetting", "WARN")
    return dict(DEFAULT_STATE)


def save_state(state: dict):
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = STATE_FILE.with_suffix(".tmp")
    raw = json.dumps(state, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    tmp.write_text(raw, encoding="utf-8")
    tmp.replace(STATE_FILE)


def acquire_lock() -> bool:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    if LOCK_FILE.is_file():
        age = time.time() - LOCK_FILE.stat().st_mtime
        if age < 300:
            log(f"Lock file exists ({int(age)}s old), another instance may be running", "WARN")
            return False
        else:
            log("Stale lock file found, removing", "WARN")
            LOCK_FILE.unlink(missing_ok=True)
    LOCK_FILE.write_text(str(os.getpid()))
    return True


def release_lock():
    LOCK_FILE.unlink(missing_ok=True)
