#!/usr/bin/env python3
"""
monitor_site.py - Near-real-time change monitor for project-skyscraper.com

Runs as a long-running daemon, polling the site in three tiers.
Detects sitemap changes, API item changes, page content changes,
media changes, and unpublished content.

Usage:
    python monitor_site.py               # Run as daemon
    python monitor_site.py --check        # Single check cycle, then exit
"""

import sys
from monitor.daemon import daemon_loop, run_single_check

if __name__ == "__main__":
    args = set(sys.argv[1:])
    if "--check" in args or "-c" in args:
        run_single_check()
    else:
        daemon_loop(quiet=False)
