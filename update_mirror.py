#!/usr/bin/env python3
"""
project-skyscraper.com - Complete Mirror Update Script
-vector_cmdr

Fully self-discovering. No hardcoded IDs, no hardcoded URL lists.

Usage:
    python update_mirror.py
"""

from monitor.fetcher import full_fetch

if __name__ == "__main__":
    full_fetch()
