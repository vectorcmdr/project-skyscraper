"""Thread-safe logging to console and file."""

import threading
from datetime import datetime, timezone

from monitor.config import REPORT_DIR, LOG_FILE

_log_lock = threading.Lock()


def _ensure_dirs():
    REPORT_DIR.mkdir(parents=True, exist_ok=True)


def log(msg: str, level: str = "INFO"):
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    line = f"[{ts}] [{level}] {msg}"
    with _log_lock:
        print(line, flush=True)
        try:
            _ensure_dirs()
            with open(str(LOG_FILE), "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except Exception:
            pass
