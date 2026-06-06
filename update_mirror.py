#!/usr/bin/env python3
"""
project-skyscraper.com - Complete Mirror Update Script
-vector_cmdr

Fully self-discovering. No hardcoded IDs, no hardcoded URL lists.

Usage:
    python update_mirror.py
"""

import os
import sys

from monitor.state_manager import acquire_lock, release_lock, STATE_FILE
from monitor.logger import log

if __name__ == "__main__":
    if not acquire_lock():
        log("Cannot acquire lock. The monitor daemon may be running. Stop it first (Ctrl+C) or wait for it to finish.", "ERROR")
        sys.exit(1)

    try:
        from monitor.fetcher import full_fetch
        full_fetch()
    finally:
        release_lock()
        if STATE_FILE.is_file():
            STATE_FILE.unlink()
            log("State file removed -- daemon will quiet-sync on next start", "FILE")
