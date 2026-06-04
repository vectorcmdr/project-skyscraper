#!/usr/bin/env python3
"""
monitor_site.py - Near-real-time change monitor for project-skyscraper.com
Companion to update_mirror.py

Runs as a long-running daemon, polling the site in three tiers.
Detects:
  - Sitemap changes (new/removed/updated pages)
  - Page/post content changes (via API modified timestamps)
  - New posts/pages/media
  - Media file replacements
  - Unattached/orphan uploads
  - Server availability issues

Notifies via:
  - Discord webhook (rich embeds for significant events)
  - Local file reports (monitor/)
  - Console output

Usage:
    python monitor_site.py               # Run as daemon
    python monitor_site.py --check        # Single check cycle, then exit
    python monitor_site.py --quiet        # Daemon mode, less console output
"""

import difflib
import json
import hashlib
import os
import random
import re
import signal
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Configuration — load from config.json, fall back to defaults
# ---------------------------------------------------------------------------

_CONFIG = {}
_config_path = Path(__file__).parent / "config.json"
if _config_path.is_file():
    try:
        _CONFIG = json.loads(_config_path.read_text(encoding="utf-8"))
    except Exception:
        _CONFIG = {}

BASE_URL = _CONFIG.get("base_url", "https://project-skyscraper.com")
MIRROR_DIR = Path(__file__).parent.resolve()
STATE_DIR = MIRROR_DIR / "state"
STATE_FILE = STATE_DIR / "monitor_state.json"
REPORT_DIR = MIRROR_DIR / "monitor"
LOG_FILE = REPORT_DIR / "monitor.log"
LOCK_FILE = STATE_DIR / ".monitor.lock"

USER_AGENT = _CONFIG.get("user_agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 project-skyscraper-monitor/1.0")

DISCORD_WEBHOOK = _CONFIG.get("discord_webhook", "")
DISCORD_PING_ID = _CONFIG.get("discord_ping_id", "")

# Git / GitHub Pages — config.json values, or sensible defaults
GIT_BRANCH = _CONFIG.get("git_branch", "main")
GIT_USER_NAME = _CONFIG.get("git_user_name", "Project Skyscraper Monitor")
GIT_USER_EMAIL = _CONFIG.get("git_user_email", "monitor@project-skyscraper.com")
GITHUB_TOKEN = _CONFIG.get("github_token", "")

POLL_INTERVALS = {
    "fast": 30,
    "medium": 120,
    "deep": 1800,
}

SITEMAP_URL = f"{BASE_URL}/sitemap.xml"
COLLECTION_ENDPOINTS = [
    # Standard wp/v2 collections (return arrays, support pagination)
    "/wp-json/wp/v2/posts",
    "/wp-json/wp/v2/pages",
    "/wp-json/wp/v2/media",
    "/wp-json/wp/v2/categories",
    "/wp-json/wp/v2/tags",
    "/wp-json/wp/v2/comments",
    "/wp-json/wp/v2/users",
    "/wp-json/wp/v2/blocks",
    "/wp-json/wp/v2/navigation",
    "/wp-json/wp/v2/menu-items",
    "/wp-json/wp/v2/menus",
    "/wp-json/wp/v2/sidebars",
    "/wp-json/wp/v2/widgets",
    "/wp-json/wp/v2/types",
    "/wp-json/wp/v2/statuses",
    "/wp-json/wp/v2/taxonomies",
    "/wp-json/wp/v2/search",
    "/wp-json/wp/v2/block-directory/search",
]

STABLE_PAGES = [
    BASE_URL,
    f"{BASE_URL}/about/",
    f"{BASE_URL}/project-skyscraper/",
]

# Hosts whose content should not trigger local-mirror-change alerts
IGNORE_HOSTS = {"i0.wp.com", "fonts.wp.com", "s0.wp.com", "stats.wp.com", "c0.wp.com"}

# Rate-limit tracking
_rate_limited_until = 0  # timestamp until which we back off
_rate_limit_lock = threading.Lock()


def _check_rate_limited() -> bool:
    """Return True if we should skip non-essential requests due to rate limiting."""
    global _rate_limited_until
    with _rate_limit_lock:
        if time.time() < _rate_limited_until:
            return True
        return False


def _mark_rate_limited(retry_after: int = 60):
    """Set global rate-limit cooldown."""
    global _rate_limited_until
    with _rate_limit_lock:
        _rate_limited_until = time.time() + max(retry_after, 30)
        log(f"Rate limited — backing off for {max(retry_after, 30)}s", "WARN")


def jitter(base: float = 0.1, spread: float = 0.15):
    """Sleep with random jitter around the base delay."""
    time.sleep(base + random.uniform(0, spread))

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

_log_lock = threading.Lock()


def _ensure_report_dir():
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    STATE_DIR.mkdir(parents=True, exist_ok=True)


def log(msg: str, level: str = "INFO"):
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    line = f"[{ts}] [{level}] {msg}"
    with _log_lock:
        print(line, flush=True)
        try:
            _ensure_report_dir()
            with open(str(LOG_FILE), "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except Exception:
            pass


# ---------------------------------------------------------------------------
# State management (JSON-based, atomic writes, git-friendly)
# ---------------------------------------------------------------------------

DEFAULT_STATE = {
    "version": 2,
    "sitemap": {
        "etag": None,
        "last_modified": None,
        "hash": None,
        "last_checked": None,
        "urls": {},
    },
    "api": {},
    "pages": {},
    "media": {},
    "stats": {
        "total_checks": 0,
        "total_changes_detected": 0,
        "first_run": None,
        "last_run": None,
    },
}

_state_lock = threading.Lock()


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
    _ensure_report_dir()
    tmp = STATE_FILE.with_suffix(".tmp")
    raw = json.dumps(state, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    tmp.write_text(raw, encoding="utf-8")
    tmp.replace(STATE_FILE)


def acquire_lock() -> bool:
    """Ensure only one monitor instance runs at a time."""
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


# ---------------------------------------------------------------------------
# HTTP helpers with conditional GET support
# ---------------------------------------------------------------------------

class FetchResult:
    __slots__ = ("url", "status", "etag", "last_modified", "content", "headers", "error")

    def __init__(self, url: str, status: int = 0, etag: str = None,
                 last_modified: str = None, content: bytes = None,
                 headers: dict = None, error: str = None):
        self.url = url
        self.status = status
        self.etag = etag
        self.last_modified = last_modified
        self.content = content
        self.headers = headers or {}
        self.error = error

    @property
    def not_modified(self) -> bool:
        return self.status == 304

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 400 and self.content is not None

    @property
    def failed(self) -> bool:
        return self.status == 0 or self.status >= 400

    @property
    def hash(self) -> str:
        if self.content is None:
            return None
        return hashlib.md5(self.content).hexdigest()

    @property
    def text(self) -> str:
        if self.content is None:
            return ""
        return self.content.decode("utf-8", errors="replace")


def fetch(url: str, etag: str = None, last_modified: str = None,
          timeout: int = 15, headers_extra: dict = None) -> FetchResult:
    """Fetch a URL with optional conditional GET headers."""
    if _check_rate_limited():
        return FetchResult(url=url, status=0, error="rate_limited")

    req_headers = {
        "User-Agent": USER_AGENT,
        "Accept": "*/*",
        **(headers_extra or {}),
    }
    if etag:
        req_headers["If-None-Match"] = etag
    if last_modified:
        req_headers["If-Modified-Since"] = last_modified

    req = urllib.request.Request(url, headers=req_headers)
    try:
        resp = urllib.request.urlopen(req, timeout=timeout)
        content = resp.read()
        result = FetchResult(
            url=url,
            status=resp.status,
            etag=resp.headers.get("ETag"),
            last_modified=resp.headers.get("Last-Modified"),
            content=content,
            headers=dict(resp.headers.items()),
        )
        if result.status == 304:
            result.content = None
        return result
    except urllib.error.HTTPError as e:
        if e.code == 304:
            return FetchResult(url=url, status=304,
                               etag=e.headers.get("ETag"),
                               last_modified=e.headers.get("Last-Modified"))
        if e.code == 429:
            retry_after = int(e.headers.get("Retry-After", 60))
            _mark_rate_limited(retry_after)
        try:
            err_content = e.read()
        except Exception:
            err_content = b""
        return FetchResult(url=url, status=e.code, content=err_content,
                           headers=dict(e.headers.items()) if e.headers else {},
                           error=str(e))
    except Exception as e:
        return FetchResult(url=url, status=0, error=str(e))


def head_url(url: str, timeout: int = 10) -> FetchResult:
    """Lightweight HEAD request."""
    if _check_rate_limited():
        return FetchResult(url=url, status=0, error="rate_limited")

    req = urllib.request.Request(url, method="HEAD", headers={
        "User-Agent": USER_AGENT,
    })
    try:
        resp = urllib.request.urlopen(req, timeout=timeout)
        return FetchResult(
            url=url, status=resp.status,
            etag=resp.headers.get("ETag"),
            last_modified=resp.headers.get("Last-Modified"),
            headers=dict(resp.headers.items()),
        )
    except urllib.error.HTTPError as e:
        if e.code == 429:
            retry_after = int(e.headers.get("Retry-After", 60))
            _mark_rate_limited(retry_after)
        return FetchResult(url=url, status=e.code, error=str(e))
    except Exception as e:
        return FetchResult(url=url, status=0, error=str(e))


# ---------------------------------------------------------------------------
# Diff computation (JSON-aware, noise-filtered)
# ---------------------------------------------------------------------------

def clean_json_for_diff(text: str) -> str:
    """Pretty-print JSON and strip auto-generated / noisy keys."""
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return text
    noisy_keys = {"_links", "_embedded", "guid", "meta", "code"}
    def _walk(v):
        if isinstance(v, dict):
            return {k: _walk(v) for k, v in v.items() if k not in noisy_keys}
        if isinstance(v, list):
            return [_walk(i) for i in v]
        return v
    data = _walk(data)
    return json.dumps(data, indent=2, sort_keys=False, ensure_ascii=False)


def compute_diff(old_bytes: bytes, new_bytes: bytes, url: str, max_lines: int = 30) -> str:
    """Unified diff of two content blobs, with JSON cleaning and truncation.

    Returns the diff text, or empty string when the only difference is
    trailing whitespace / newline (suppressed — not structurally meaningful).
    """
    old_txt = old_bytes.decode("utf-8", errors="replace")
    new_txt = new_bytes.decode("utf-8", errors="replace")

    # JSON endpoints get cleaned and pretty-printed
    if "wp-json" in url or url.endswith(".json"):
        old_txt = clean_json_for_diff(old_txt)
        new_txt = clean_json_for_diff(new_txt)

    old_lines = old_txt.splitlines()
    new_lines = new_txt.splitlines()
    diff_gen = difflib.unified_diff(old_lines, new_lines, lineterm="", n=0)
    diff_lines = list(diff_gen)[2:]  # skip ---/+++ header

    # No structural diff but raw bytes differ — trailing whitespace or newline only
    if not diff_lines and old_txt != new_txt:
        return ""

    if len(diff_lines) > max_lines:
        diff_lines = diff_lines[:max_lines]
        diff_lines.append(f"... ({len(diff_lines) - max_lines + 1} more lines)")

    diff_text = "\n".join(diff_lines)
    # Truncate to stay under Discord field limit (1024 chars)
    if len(diff_text) > 1000:
        diff_text = diff_text[:997] + "..."
    return diff_text


def _strip_html(text: str) -> str:
    """Strip HTML tags from text while preserving diff +/- markers and newlines."""
    return re.sub(r'<[^>]+>', '', text)


def _diff_and_store(url: str, subdir: str, change_obj: dict, item_key: str = "diffs"):
    """Fetch URL, diff old vs new content, and store result in change_obj."""
    path = Path(url_to_path(url, subdir=subdir))
    old_bytes = path.read_bytes() if path.is_file() else None
    fetch_and_save(url, subdir)
    new_bytes = path.read_bytes() if path.is_file() else None
    if old_bytes is not None and new_bytes is not None and old_bytes != new_bytes:
        diff = compute_diff(old_bytes, new_bytes, url)
        change_obj.setdefault(item_key, []).append({
            "url": url,
            "diff": diff,
        })


# ---------------------------------------------------------------------------
# Discord notification
# ---------------------------------------------------------------------------

# Module-level: first embed this cycle gets the ping content attached
_embed_count = 0


def send_discord(title: str, description: str, fields: list = None,
                 color: int = 0x00ff88, url: str = None):
    """Send a rich embed to the Discord webhook.
    The first embed sent each notify cycle includes the @mention ping."""
    global _embed_count

    if not DISCORD_WEBHOOK:
        return

    _embed_count += 1

    embed = {
        "title": title[:256],
        "description": description[:4096],
        "color": color,
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        "footer": {"text": "Project Skyscraper Monitor"},
    }
    if url:
        embed["url"] = url
    if fields:
        embed["fields"] = [
            {"name": f["name"][:256], "value": f["value"][:1024], "inline": f.get("inline", False)}
            for f in fields[:25]
        ]

    payload = {"embeds": [embed]}

    # Attach ping text to the FIRST embed so it appears above the content
    if _embed_count == 1 and DISCORD_WEBHOOK and DISCORD_PING_ID:
        payload["content"] = f"ATT: IWTS Operator <@{DISCORD_PING_ID}>"

    req = urllib.request.Request(
        DISCORD_WEBHOOK,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
        },
        method="POST",
    )
    try:
        resp = urllib.request.urlopen(req, timeout=10)
        log(f"Discord notification sent: {title[:60]}", "DISCORD")
        return True
    except urllib.error.HTTPError as e:
        log(f"Discord send failed ({e.code}): {e.read()[:200]}", "ERROR")
    except Exception as e:
        log(f"Discord send failed: {e}", "ERROR")
    return False


def send_discord_simple(message: str, color: int = 0x00ff88):
    """Send a simple embed to Discord."""
    if not DISCORD_WEBHOOK:
        return

    payload = {
        "embeds": [{
            "title": message[:256],
            "color": color,
            "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        }],
    }

    req = urllib.request.Request(
        DISCORD_WEBHOOK,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
        },
        method="POST",
    )
    try:
        resp = urllib.request.urlopen(req, timeout=10)
        return True
    except Exception as e:
        log(f"Discord send failed: {e}", "ERROR")
    return False


def send_discord_ping():
    """Send a text ping message to alert the operator (separate from embeds)."""
    if not DISCORD_WEBHOOK or not DISCORD_PING_ID:
        return
    payload = {
        "content": f"ATT: IWTS Operator <@{DISCORD_PING_ID}>",
    }
    req = urllib.request.Request(
        DISCORD_WEBHOOK,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
        },
        method="POST",
    )
    try:
        resp = urllib.request.urlopen(req, timeout=10)
        log("Discord ping sent", "DISCORD")
        return True
    except Exception as e:
        log(f"Discord ping failed: {e}", "ERROR")
    return False


# ---------------------------------------------------------------------------
# Report file writer
# ---------------------------------------------------------------------------

def write_report(category: str, data: dict):
    """Write a structured JSON report to the monitor/ directory."""
    _ensure_report_dir()
    ts = datetime.now(timezone.utc)
    filename = f"{ts.strftime('%Y%m%d_%H%M%S')}_{category}.json"
    path = REPORT_DIR / filename
    report = {
        "timestamp": ts.strftime("%Y-%m-%d %H:%M:%S UTC"),
        "category": category,
        "data": data,
    }
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    log(f"Report written: {path.name}", "FILE")

    # Also write a latest pointer
    latest = REPORT_DIR / f"latest_{category}.json"
    latest.write_text(path.name, encoding="utf-8")


def cleanup_old_reports(max_age_hours: int = 72):
    """Remove report files older than max_age_hours."""
    cutoff = time.time() - (max_age_hours * 3600)
    for f in REPORT_DIR.glob("*.json"):
        if f.name.startswith("latest_"):
            continue
        try:
            if f.stat().st_mtime < cutoff:
                f.unlink()
        except OSError:
            pass


# ---------------------------------------------------------------------------
# Sitemap check
# ---------------------------------------------------------------------------

def parse_sitemap_urls(content: str) -> dict:
    """Parse sitemap XML content, return dict of {url: metadata}."""
    urls = {}
    try:
        root = ET.fromstring(content)
        ns = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
        for url_elem in root.findall(".//sm:url", ns):
            loc = url_elem.find("sm:loc", ns)
            lastmod = url_elem.find("sm:lastmod", ns)
            if loc is not None and loc.text:
                urls[loc.text.strip()] = {
                    "lastmod": lastmod.text.strip() if lastmod is not None and lastmod.text else None,
                    "type": "page",
                }
        # Check for image sitemaps too
        img_ns = {**ns, "image": "http://www.google.com/schemas/sitemap-image/1.1"}
        for url_elem in root.findall(".//sm:url", img_ns):
            loc = url_elem.find("sm:loc", ns)
            if loc is not None and loc.text:
                url_str = loc.text.strip()
                if url_str not in urls:
                    urls[url_str] = {"lastmod": None, "type": "page"}
    except ET.ParseError:
        pass
    return urls


def check_sitemap(state: dict) -> list:
    """Fetch and diff the sitemap. Returns list of change dicts for URL diffs only."""
    changes = []
    sitemap_state = state.setdefault("sitemap", {})
    sitemap_state.setdefault("urls", {})

    etag = sitemap_state.get("etag")
    last_modified = sitemap_state.get("last_modified")

    result = fetch(SITEMAP_URL, etag=etag, last_modified=last_modified)

    if result.not_modified:
        log("Sitemap: unchanged (304)", "FAST")
        sitemap_state["last_checked"] = datetime.now(timezone.utc).isoformat()
        return changes

    if result.failed:
        log(f"Sitemap fetch failed: {result.status} {result.error}", "WARN")
        changes.append({
            "type": "sitemap_error",
            "detail": f"HTTP {result.status}: {result.error}",
        })
        return changes

    # Parse URLs and diff against previous set
    new_urls = parse_sitemap_urls(result.text)
    old_urls = sitemap_state.get("urls", {})

    old_url_set = set(old_urls.keys())
    new_url_set = set(new_urls.keys())

    added = new_url_set - old_url_set
    removed = old_url_set - new_url_set

    if added:
        changes.append({
            "type": "sitemap_added",
            "count": len(added),
            "urls": sorted(added)[:50],
            "detail": f"Added {len(added)} URL(s) to sitemap",
        })
    if removed:
        changes.append({
            "type": "sitemap_removed",
            "count": len(removed),
            "urls": sorted(removed)[:50],
            "detail": f"Removed {len(removed)} URL(s) from sitemap",
        })

    if not added and not removed:
        log("Sitemap: content refreshed but no URL changes", "FAST")
    else:
        log(f"Sitemap: +{len(added)} -{len(removed)}", "FAST")

    # Update state
    sitemap_state["etag"] = result.etag
    sitemap_state["last_modified"] = result.last_modified
    sitemap_state["last_checked"] = datetime.now(timezone.utc).isoformat()
    sitemap_state["urls"] = new_urls

    return changes


# ---------------------------------------------------------------------------
# API collection checks
# ---------------------------------------------------------------------------

def _fetch_all_pages(base_url: str, per_page: int = 100) -> tuple:
    """Fetch an API endpoint with pagination. Returns (items, combined_hash, total_pages, etag, last_modified)."""
    all_items = []
    page = 1
    total_pages = 1
    final_etag = None
    final_last_modified = None
    all_raw = b""

    url = f"{base_url}?per_page={per_page}"
    result = fetch(url)
    if result.failed:
        return [], "", 0, None, None
    if result.content:
        all_raw = result.content

    final_etag = result.etag
    final_last_modified = result.last_modified

    try:
        items = json.loads(result.text)
    except json.JSONDecodeError:
        return [], "", 0, None, None

    if not isinstance(items, list):
        return [], "", 0, None, None

    all_items.extend(items)

    # Check for more pages via X-WP-TotalPages
    try:
        total_pages = int(result.headers.get("X-WP-TotalPages", 1))
    except (ValueError, TypeError):
        total_pages = 1

    # Fetch remaining pages
    for page in range(2, total_pages + 1):
        page_url = f"{base_url}?per_page={per_page}&page={page}"
        jitter(0.15, 0.1)
        pr = fetch(page_url)
        if pr.ok and pr.content:
            all_raw += pr.content
            try:
                page_items = json.loads(pr.text)
                if isinstance(page_items, list):
                    all_items.extend(page_items)
            except json.JSONDecodeError:
                pass

    combined_hash = hashlib.md5(all_raw).hexdigest()
    return all_items, combined_hash, total_pages, final_etag, final_last_modified


def check_api_collection(endpoint: str, state: dict) -> list:
    """Fetch a wp/v2 collection endpoint and diff the item list."""
    changes = []
    url = f"{BASE_URL}{endpoint}"
    api_state = state.setdefault("api", {}).setdefault(endpoint, {})

    etag = api_state.get("etag")
    last_modified = api_state.get("last_modified")

    # Try with conditional GET first (no per_page for ETag consistency)
    result = fetch(url, etag=etag, last_modified=last_modified)

    if result.not_modified:
        log(f"API {endpoint}: unchanged (304)", "MEDIUM")
        api_state["last_checked"] = datetime.now(timezone.utc).isoformat()
        return changes

    # Full fetch with pagination
    items, new_hash, total_pages, new_etag, new_last_modified = _fetch_all_pages(url)

    if not items:
        log(f"API {endpoint}: fetch failed or empty", "WARN")
        return changes

    # Get known items from state
    known_items = {str(i["id"]): i for i in api_state.get("items", [])} if isinstance(api_state.get("items"), list) else {}
    known_ids = set(known_items.keys())
    new_ids = set()
    new_items_map = {}

    # Track items seen in previous runs (for stability buffer)
    pending_items = set(api_state.get("_pending_ids", []))

    for item in items:
        iid = str(item.get("id"))
        if iid:
            new_ids.add(iid)
            new_items_map[iid] = {
                "id": item["id"],
                "title": item.get("title", {}).get("rendered", "") if isinstance(item.get("title"), dict) else "",
                "modified": item.get("modified", ""),
                "type": item.get("type", ""),
                "status": item.get("status", ""),
                "link": item.get("link", ""),
                "author": item.get("author", 0),
                "name": item.get("name", ""),
            }

    log(f"API {endpoint}: {len(new_ids)} items across {total_pages} page(s)", "DEBUG")

    added_ids = set()
    for iid in new_ids - known_ids:
        # Require the item to be seen twice before reporting as "added"
        if iid in pending_items:
            added_ids.add(iid)
        else:
            pending_items.add(iid)

    removed_ids = known_ids - new_ids
    changed_items = []

    for iid in new_ids & known_ids:
        new_item = new_items_map[iid]
        old_item = known_items.get(iid, {})
        if new_item.get("modified") and old_item.get("modified") and new_item["modified"] != old_item["modified"]:
            changed_items.append((iid, old_item, new_item))
        elif new_item.get("modified") and not old_item.get("modified"):
            changed_items.append((iid, old_item, new_item))

    if added_ids:
        added_details = [new_items_map[iid] for iid in sorted(added_ids)]
        changes.append({
            "type": "api_items_added",
            "endpoint": endpoint,
            "count": len(added_ids),
            "items": added_details[:30],
            "detail": f"{len(added_ids)} new item(s) in {endpoint}",
        })
        log(f"API {endpoint}: +{len(added_ids)} new items: "
            f"{', '.join(str(n['id']) for n in added_details[:10])}", "MEDIUM")

    if removed_ids:
        changes.append({
            "type": "api_items_removed",
            "endpoint": endpoint,
            "count": len(removed_ids),
            "ids": sorted(removed_ids, key=int)[:30],
            "detail": f"{len(removed_ids)} item(s) removed from {endpoint}",
        })
        log(f"API {endpoint}: -{len(removed_ids)} items removed", "MEDIUM")

    if changed_items:
        changes.append({
            "type": "api_items_modified",
            "endpoint": endpoint,
            "count": len(changed_items),
            "items": [
                {
                    "id": c[2]["id"],
                    "title": c[2]["title"],
                    "author": c[2].get("author", 0),
                    "old_modified": c[1].get("modified", ""),
                    "new_modified": c[2]["modified"],
                }
                for c in changed_items[:30]
            ],
            "detail": f"{len(changed_items)} item(s) modified in {endpoint}",
        })
        log(f"API {endpoint}: ~{len(changed_items)} modified items", "MEDIUM")

    if not added_ids and not removed_ids and not changed_items:
        log(f"API {endpoint}: hash changed but no item diff (meta only)", "MEDIUM")

    # Update state
    api_state["etag"] = new_etag or result.etag
    api_state["last_modified"] = new_last_modified or result.last_modified
    api_state["hash"] = new_hash
    api_state["last_checked"] = datetime.now(timezone.utc).isoformat()
    api_state["items"] = [new_items_map[iid] for iid in sorted(new_items_map, key=int)]
    api_state["total_pages"] = total_pages
    # Clean up confirmed additions from pending
    api_state["_pending_ids"] = sorted(iid for iid in pending_items if iid not in added_ids)

    return changes


# ---------------------------------------------------------------------------
# Page content checks
# ---------------------------------------------------------------------------

def check_page_content(url: str, state: dict) -> list:
    """Fetch a single page and compare hash. Returns changes if content differs."""
    changes = []
    page_state = state.setdefault("pages", {}).setdefault(url, {})

    etag = page_state.get("etag")
    last_modified = page_state.get("last_modified")

    result = fetch(url, etag=etag, last_modified=last_modified)

    if result.not_modified:
        page_state["last_checked"] = datetime.now(timezone.utc).isoformat()
        return changes

    if result.failed:
        log(f"Page {url}: fetch failed ({result.status})", "WARN")
        return changes

    new_hash = result.hash
    old_hash = page_state.get("hash")

    if old_hash is not None and old_hash != new_hash:
        changes.append({
            "type": "page_content_changed",
            "url": url,
            "old_hash": old_hash,
            "new_hash": new_hash,
            "detail": f"Content changed: {url}",
        })
        log(f"Page content CHANGED: {url}", "DEEP")
    elif old_hash is None:
        log(f"Page content first tracked: {url}", "DEEP")

    page_state["etag"] = result.etag
    page_state["last_modified"] = result.last_modified
    page_state["hash"] = new_hash
    page_state["last_checked"] = datetime.now(timezone.utc).isoformat()

    return changes


# ---------------------------------------------------------------------------
# Media checks
# ---------------------------------------------------------------------------

def check_media_items(state: dict) -> list:
    """Check media items for unattached/orphan uploads, replaced files, and thumbnail changes."""
    changes = []
    media_endpoint = "/wp-json/wp/v2/media"
    api_state = state.setdefault("api", {}).setdefault(media_endpoint, {})
    known_items = {str(i["id"]): i for i in api_state.get("items", [])} if isinstance(api_state.get("items"), list) else {}

    # Thumbnail tracker state
    thumb_state = state.setdefault("media_thumbnails", {})

    # Get items from state (already fetched in API collection check)
    items_list = api_state.get("items", [])
    if not items_list:
        return changes

    for item in items_list:
        iid = str(item.get("id"))
        old = known_items.get(iid)

        # Check for unattached media (post_parent == 0)
        if item.get("post_parent") == 0:
            if not old or old.get("post_parent") != 0 or True:
                pass  # Already reported via API collection check

        # Check if media URL changed (file replacement)
        old_old = {} if not old else old
        old_url = old_old.get("source_url")
        new_url = item.get("source_url")
        if old_url and new_url and old_url != new_url:
            changes.append({
                "type": "media_replaced",
                "id": item["id"],
                "old_url": old_url,
                "new_url": new_url,
                "detail": f"Media #{item['id']} file replaced",
            })
            log(f"Media #{item['id']} file replaced: {old_url} -> {new_url}", "DEEP")

        # Check if new media was uploaded without a post
        if item.get("post_parent") == 0 and not old:
            changes.append({
                "type": "media_orphan_upload",
                "id": item["id"],
                "title": item.get("title", ""),
                "url": item.get("source_url"),
                "author": item.get("author", 0),
                "detail": f"New unattached media #{item['id']} uploaded: {item.get('title', '')}",
            })
            log(f"Orphan media #{item['id']}: {item.get('title', '')}", "DEEP")

        # Check thumbnail variants
        media_details = item.get("media_details", {})
        sizes = media_details.get("sizes", {}) if isinstance(media_details, dict) else {}
        known_thumbs = thumb_state.get(iid, {})
        current_thumbs = {}

        for size_name, size_info in sizes.items():
            if isinstance(size_info, dict):
                src_url = size_info.get("source_url", "")
                if src_url:
                    # Get existing entry for this size (default to empty dict)
                    thumb_entry = known_thumbs.get(size_name, {})
                    if not isinstance(thumb_entry, dict):
                        thumb_entry = {}
                    old_etag = thumb_entry.get("etag")
                    result = head_url(src_url, timeout=8)
                    if result.ok:
                        new_etag = result.etag or result.hash
                        current_thumbs[size_name] = {"url": src_url, "etag": new_etag}
                        if old_etag and old_etag != new_etag:
                            changes.append({
                                "type": "media_thumbnail_changed",
                                "id": item["id"],
                                "media_title": item.get("title", ""),
                                "size": size_name,
                                "url": src_url,
                                "detail": f"Media #{item['id']} thumbnail '{size_name}' changed",
                            })
                            log(f"Media #{item['id']} thumbnail '{size_name}' changed: {src_url}", "DEEP")
                        elif not old_etag:
                            log(f"Media #{item['id']} thumbnail '{size_name}' first tracked: {src_url}", "DEEP")
                    time.sleep(0.05)

        if current_thumbs:
            thumb_state[iid] = current_thumbs

    return changes


# ---------------------------------------------------------------------------
# Orphan ID probing (lightweight)
# ---------------------------------------------------------------------------

def probe_unpublished(state: dict) -> list:
    """Probe for unpublished IDs near the max known ID, in chunks per cycle."""
    changes = []
    max_id = 0

    for ep in ["/wp-json/wp/v2/posts", "/wp-json/wp/v2/pages"]:
        api_state = state.get("api", {}).get(ep, {})
        items = api_state.get("items", [])
        for item in items:
            iid = item.get("id", 0)
            if iid > max_id:
                max_id = iid

    if max_id == 0:
        return changes

    probe_range = 300
    chunk_size = 30

    # Get or init probe position
    probe_state = state.setdefault("probe", {})
    probe_pos = probe_state.get("position", max_id + 1)
    probe_ceiling = max_id + probe_range

    # If we've passed the ceiling, reset
    if probe_pos > probe_ceiling:
        probe_pos = max_id + 1

    chunk_end = min(probe_pos + chunk_size - 1, probe_ceiling)

    for pid in range(probe_pos, chunk_end + 1):
        for ep_template in ["/wp-json/wp/v2/posts/{id}", "/wp-json/wp/v2/pages/{id}"]:
            url = f"{BASE_URL}{ep_template.replace('{id}', str(pid))}"
            result = head_url(url, timeout=8)
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
                log(f"Newly published {ep_template.split('/')[-1].split('{')[0]} #{pid} (was hidden)", "DEEP")

        jitter(0.08, 0.1)

    # Advance position
    probe_state["position"] = chunk_end + 1
    probe_state["last_probed"] = datetime.now(timezone.utc).isoformat()

    log(f"Probe: checked IDs {probe_pos}-{chunk_end} (next: {chunk_end + 1}, ceiling: {probe_ceiling})", "DEEP")

    return changes


# ---------------------------------------------------------------------------
# Generic JSON endpoint check (for non-paginated API endpoints)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# URL-to-path mapping (mirrors update_mirror.py's url_to_path)
# ---------------------------------------------------------------------------

def url_to_path(url: str, subdir: str = "") -> Path:
    """Map a URL to a local file path matching update_mirror.py conventions."""
    if url.startswith("//"):
        url = "https:" + url
    parsed = urllib.parse.urlparse(url)
    path_str = parsed.path.rstrip("/") or "/"
    q = parsed.query
    if q:
        qs = q.replace("&", "_").replace("=", "_").replace("%", "").replace(";", "_").replace(" ", "_")
        path_str = path_str + "_" + qs[:120]
    if path_str.endswith("/") or path_str == "":
        path_str += "index"
    ext = Path(urllib.parse.unquote(path_str)).suffix
    if not ext:
        if "wp-json" in url or "oembed" in url or parsed.path.startswith("/wp-json"):
            path_str += ".json"
        else:
            path_str += ".html"
    path_str = path_str.replace("https:", "").replace("http:", "")
    if path_str.startswith("/"):
        path_str = path_str[1:]
    path_str = re.sub(r'[<>:"\\|?*]', "_", path_str)
    parts = [p[:200] for p in path_str.replace("\\", "/").split("/")]
    return MIRROR_DIR / subdir / "/".join(parts)


def _is_binary_url(url: str) -> bool:
    """Guess if a URL points to a binary file."""
    ext = Path(urllib.parse.urlparse(url).path).suffix.lower()
    return ext in {".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".svg",
                   ".woff2", ".woff", ".ttf", ".eot", ".otf",
                   ".zip", ".gz", ".pdf", ".mp4", ".webm", ".mp3"}


def fetch_and_save(url: str, subdir: str = "") -> bool:
    """Fetch a URL and save its content to the mirror using url_to_path."""
    binary = _is_binary_url(url)
    path = url_to_path(url, subdir=subdir)
    path.parent.mkdir(parents=True, exist_ok=True)

    # Check if we already have this content (MD5 skip)
    old_bytes = path.read_bytes() if path.is_file() else None

    result = fetch(url)
    if result.failed:
        log(f"  FETCH FAIL: {url} -> {result.status}", "ERROR")
        return False
    if result.content is None:
        return False

    # Skip if unchanged
    if old_bytes is not None and hashlib.md5(old_bytes).hexdigest() == result.hash:
        log(f"  FETCH SKIP (unchanged): {url}", "FETCH")
        return True

    path.write_bytes(result.content)
    log(f"  FETCH OK: {url} -> {path.relative_to(MIRROR_DIR)}", "FETCH")
    return True


def apply_changes(changes: list):
    """Surgically fetch and save content for every detected change type."""
    for change in changes:
        ctype = change["type"]

        if ctype == "sitemap_added":
            for page_url in change.get("urls", []):
                if page_url.startswith(BASE_URL):
                    fetch_and_save(page_url, "html")
                    jitter(0.2, 0.1)

        elif ctype == "api_items_added":
            endpoint = change.get("endpoint", "")
            for item in change.get("items", []):
                iid = item.get("id")
                if not iid:
                    continue
                # Determine sub-endpoint from collection name
                if "/posts" in endpoint:
                    api_url = f"{BASE_URL}/wp-json/wp/v2/posts/{iid}"
                    fetch_and_save(api_url, "api")
                    jitter(0.15, 0.1)
                    # Also fetch the HTML page if we have a link
                    link = item.get("link", "")
                    if link and link.startswith(BASE_URL):
                        fetch_and_save(link, "html")
                        jitter(0.2, 0.1)
                elif "/pages" in endpoint:
                    api_url = f"{BASE_URL}/wp-json/wp/v2/pages/{iid}"
                    fetch_and_save(api_url, "api")
                    jitter(0.15, 0.1)
                    link = item.get("link", "")
                    if link and link.startswith(BASE_URL):
                        fetch_and_save(link, "html")
                        jitter(0.2, 0.1)
                elif "/media" in endpoint:
                    api_url = f"{BASE_URL}/wp-json/wp/v2/media/{iid}"
                    fetch_and_save(api_url, "api")
                    jitter(0.15, 0.1)
                    # Fetch the media source_url if available
                    media_url = item.get("url") or item.get("source_url", "")
                    if media_url:
                        fetch_and_save(media_url, "media")
                        jitter(0.2, 0.1)

        elif ctype == "api_items_modified":
            endpoint = change.get("endpoint", "")
            for item in change.get("items", []):
                iid = item.get("id")
                if not iid:
                    continue
                if "/posts" in endpoint:
                    api_url = f"{BASE_URL}/wp-json/wp/v2/posts/{iid}"
                    _diff_and_store(api_url, "api", change)
                    jitter(0.15, 0.1)
                    link = item.get("link", "")
                    if link and link.startswith(BASE_URL):
                        _diff_and_store(link, "html", change)
                        jitter(0.2, 0.1)
                elif "/pages" in endpoint:
                    api_url = f"{BASE_URL}/wp-json/wp/v2/pages/{iid}"
                    _diff_and_store(api_url, "api", change)
                    jitter(0.15, 0.1)
                    link = item.get("link", "")
                    if link and link.startswith(BASE_URL):
                        _diff_and_store(link, "html", change)
                        jitter(0.2, 0.1)

        elif ctype == "page_content_changed":
            page_url = change.get("url", "")
            if page_url and page_url.startswith(BASE_URL):
                _diff_and_store(page_url, "html", change)
                jitter(0.2, 0.1)

        elif ctype == "media_thumbnail_changed":
            thumb_url = change.get("url", "")
            if thumb_url:
                fetch_and_save(thumb_url, "media")
                jitter(0.15, 0.1)

        elif ctype == "media_replaced":
            new_url = change.get("new_url", "")
            if new_url:
                fetch_and_save(new_url, "media")
                jitter(0.2, 0.1)
            # Also re-fetch the media API endpoint to update metadata
            mid = change.get("id")
            if mid:
                api_url = f"{BASE_URL}/wp-json/wp/v2/media/{mid}"
                fetch_and_save(api_url, "api")
                jitter(0.15, 0.1)


# Change types that warrant a feed entry and git commit (vs. ephemeral metadata noise)
MEANINGFUL_CHANGE_TYPES = {
    "api_items_added",
    "api_items_modified",
    "api_items_removed",
    "media_orphan_upload",
    "media_replaced",
    "media_thumbnail_changed",
    "page_content_changed",
    "sitemap_added",
    "sitemap_removed",
    "unpublished_detected",
}


# ---------------------------------------------------------------------------
# Check cycle orchestrator
# ---------------------------------------------------------------------------

def run_check_cycle(state: dict, tiers: set = None) -> list:
    """Run one cycle of checks. Returns list of all detected changes."""
    all_changes = []

    if tiers is None:
        tiers = {"fast", "medium", "deep"}

    state["stats"]["total_checks"] += 1
    state["stats"]["last_run"] = datetime.now(timezone.utc).isoformat()
    if state["stats"]["first_run"] is None:
        state["stats"]["first_run"] = state["stats"]["last_run"]

    # -- Fast tier: sitemap HEAD --
    if "fast" in tiers:
        log("=== Fast check ===", "FAST")
        changes = check_sitemap(state)
        all_changes.extend(changes)

    # -- Medium tier: API collections + stable pages HEAD --
    if "medium" in tiers:
        log("=== Medium check ===", "MEDIUM")
        with ThreadPoolExecutor(max_workers=8) as ex:
            futures = {}
            for ep in COLLECTION_ENDPOINTS:
                futures[ex.submit(check_api_collection, ep, state)] = ep

            for f in as_completed(futures):
                ep = futures[f]
                try:
                    changes = f.result()
                    all_changes.extend(changes)
                except Exception as e:
                    log(f"Error checking {ep}: {e}", "ERROR")

    # -- Deep tier: page content checks + media analysis + orphan probe --
    if "deep" in tiers:
        log("=== Deep check ===", "DEEP")

        # Check sitemap-listed pages for content changes
        sitemap_urls = state.get("sitemap", {}).get("urls", {})
        known_pages = list(sitemap_urls.keys())[:5]  # Limit to 5 per deep cycle to avoid hammering

        if known_pages:
            with ThreadPoolExecutor(max_workers=3) as ex:
                futures = {}
                for page_url in known_pages:
                    futures[ex.submit(check_page_content, page_url, state)] = page_url
                    jitter(0.05, 0.05)

                for f in as_completed(futures):
                    page_url = futures[f]
                    try:
                        changes = f.result()
                        all_changes.extend(changes)
                    except Exception as e:
                        log(f"Error checking page {page_url}: {e}", "ERROR")

        # Media analysis
        try:
            changes = check_media_items(state)
            all_changes.extend(changes)
        except Exception as e:
            log(f"Error checking media: {e}", "ERROR")

        # Orphan probe (light)
        try:
            changes = probe_unpublished(state)
            all_changes.extend(changes)
        except Exception as e:
            log(f"Error probing unpublished: {e}", "ERROR")

    # Save state
    save_state(state)

    # If changes found, fetch content + notify + push
    if all_changes:
        state["stats"]["total_changes_detected"] += len(all_changes)
        log(f"=== Fetching content for {len(all_changes)} change(s) ===", "FETCH")
        apply_changes(all_changes)
        notify_changes(all_changes)
        generate_page_data(state, all_changes)
        meaningful_changes = [c for c in all_changes if c.get("type") in MEANINGFUL_CHANGE_TYPES]
        if meaningful_changes:
            git_push_site()
        else:
            log(f"All {len(all_changes)} change(s) are noise-only — skipping git commit", "CHECK")
    else:
        log("No changes detected", "CHECK")

    return all_changes


# ---------------------------------------------------------------------------
# GitHub Pages data generation
# ---------------------------------------------------------------------------

def _build_user_map(state: dict) -> dict:
    """Build {user_id: display_name} lookup from state's users endpoint data."""
    users = state.get("api", {}).get("/wp-json/wp/v2/users", {}).get("items", [])
    result = {}
    for u in users:
        uid = u.get("id", 0)
        name = u.get("name") or u.get("title") or ""
        if not name:
            name = str(uid)
        result[uid] = name
    return result


def _change_to_feed_entry(c: dict) -> dict | None:
    """Convert a change object into a feed entry dict."""
    t = c["type"]
    now = datetime.now(timezone.utc).isoformat()
    link = ""
    title = c.get("detail", "unknown")
    endpoint = ""
    author = 0

    if t == "sitemap_added":
        count = c.get("count", 0)
        urls = c.get("urls", [])
        title = urls[0] if urls else f"{count} URL(s) added"
        link = urls[0] if urls else ""
        endpoint = "sitemap"
    elif t == "sitemap_removed":
        count = c.get("count", 0)
        urls = c.get("urls", [])
        title = urls[0] if urls else f"{count} URL(s) removed"
        link = urls[0] if urls else ""
        endpoint = "sitemap"
    elif t == "api_items_added":
        items = c.get("items", [])
        title = items[0].get("title", "") if items else c.get("detail", "")
        link = items[0].get("link", "") if items else ""
        author = items[0].get("author", 0) if items else 0
        endpoint = c.get("endpoint", "")
    elif t == "api_items_removed":
        ids = c.get("ids", [])
        title = c.get("detail", f"{len(ids)} item(s) removed")
        endpoint = c.get("endpoint", "")
    elif t == "api_items_modified":
        items = c.get("items", [])
        title = items[0].get("title", "") if items else c.get("detail", "")
        link = items[0].get("link", "") if items else ""
        author = items[0].get("author", 0) if items else 0
        endpoint = c.get("endpoint", "")
    elif t == "page_content_changed":
        title = c.get("url", "").split("/")[-1] or "page"
        link = c.get("url", "")
        endpoint = "page"
    elif t == "media_replaced":
        title = f"Media #{c.get('id', '?')}"
        link = c.get("new_url", "")
        endpoint = "media"
    elif t == "media_thumbnail_changed":
        title = f"Media #{c.get('id', '?')} {c.get('size', '')}"
        link = c.get("url", "")
        endpoint = "media"
    elif t == "media_orphan_upload":
        title = c.get("title", f"Media #{c.get('id', '?')}")
        link = c.get("url", "")
        author = c.get("author", 0)
        endpoint = "media"
    elif t == "unpublished_detected":
        title = f"#{c.get('id', '?')} ({c.get('endpoint', '')})"
        endpoint = "probe"
    else:
        return None

    return {
        "type": t,
        "timestamp": now,
        "title": title,
        "link": link,
        "endpoint": endpoint,
        "diff": c.get("diff", ""),
        "detail": c.get("detail", ""),
        "author": author,
    }


def _update_manifest(manifest: dict, c: dict):
    """Update the manifest dict from a change object."""
    t = c["type"]
    if t in ("api_items_added", "api_items_modified"):
        for item in c.get("items", []):
            path = urllib.parse.urlparse(item.get("link", "")).path
            if not path:
                continue
            existing = [p for p in manifest["pages"] if p["path"] == path]
            entry = {
                "path": path,
                "title": item.get("title", path.split("/")[-1] or path),
                "type": item.get("type", "page"),
                "modified": item.get("modified", ""),
                "date_gmt": item.get("date_gmt", ""),
                "author": item.get("author", 0),
            }
            if existing:
                existing[0].update(entry)
            else:
                manifest["pages"].append(entry)

    elif t == "api_items_removed":
        ids = c.get("ids", [])
        # Match by extracting ID from path — fragile, skip for now
        pass

    elif t in ("sitemap_added", "sitemap_removed"):
        # Sitemap changes handled by sync_sitemap_to_manifest below
        pass

    elif t == "page_content_changed":
        url = c.get("url", "")
        if url:
            path = urllib.parse.urlparse(url).path
            for p in manifest["pages"]:
                if p["path"] == path:
                    p["modified"] = datetime.now(timezone.utc).isoformat()
                    break


def generate_page_data(state: dict, changes: list):
    """Generate feed.json (change log) and manifest.json (known pages)
    for the GitHub Pages static site under docs/."""
    SITE_DIR = MIRROR_DIR / "docs"
    DATA_DIR = SITE_DIR / "data"
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    feed_path = DATA_DIR / "feed.json"
    manifest_path = DATA_DIR / "manifest.json"

    # ── FEED ──────────────────────────────────────────────
    feed = {"entries": []}
    if feed_path.is_file():
        try:
            feed = json.loads(feed_path.read_text(encoding="utf-8"))
        except Exception:
            feed = {"entries": []}

    for c in changes:
        entry = _change_to_feed_entry(c)
        if entry:
            feed["entries"].append(entry)

    feed["entries"] = feed["entries"][-500:]
    feed["updated"] = datetime.now(timezone.utc).isoformat()

    # ── MANIFEST ──────────────────────────────────────────
    manifest = {"pages": []}
    if manifest_path.is_file():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception:
            manifest = {"pages": []}

    existing_paths = {p["path"] for p in manifest["pages"]}

    # Update manifest from API change objects (adds titles/dates)
    for c in changes:
        _update_manifest(manifest, c)

    # Sync sitemap URLs into manifest (adds new, removes stale)
    sitemap_urls = state.get("sitemap", {}).get("urls", {})
    sitemap_paths = set()
    for url, meta in (sitemap_urls or {}).items():
        path = urllib.parse.urlparse(url).path
        sitemap_paths.add(path)
        if path not in existing_paths:
            manifest["pages"].append({
                "path": path,
                "title": path.strip("/").split("/")[-1].replace("-", " ").title(),
                "type": "page",
                "modified": meta.get("lastmod", ""),
                "date_gmt": "",
                "author": 0,
            })

    # Remove pages no longer in sitemap (unpublished) — only if we have sitemap data
    if sitemap_paths:
        manifest["pages"] = [p for p in manifest["pages"]
                             if p["path"] in sitemap_paths or p["type"] != "page"]

    # Re-sort by modified descending
    manifest["pages"].sort(key=lambda p: p.get("modified", ""), reverse=True)

    # Resolve author IDs to display names
    user_map = _build_user_map(state)
    for entry in feed["entries"]:
        aid = entry.get("author", 0)
        entry["author"] = user_map.get(aid, "") if aid else ""
    for p in manifest["pages"]:
        aid = p.get("author", 0)
        p["author"] = user_map.get(aid, "") if aid else ""

    # Write feed only if entries actually changed (skip mere timestamp bumps)
    feed_written = False
    if feed_path.is_file():
        try:
            old_feed = json.loads(feed_path.read_text(encoding="utf-8"))
            if old_feed.get("entries") == feed["entries"]:
                feed_written = True  # already identical, no need to write
        except Exception:
            pass
    if not feed_written:
        feed_path.write_text(json.dumps(feed, indent=2, ensure_ascii=False), encoding="utf-8")
        feed_written = True

    # Write manifest only if pages list actually changed
    manifest_written = False
    manifest["updated"] = datetime.now(timezone.utc).isoformat()
    if manifest_path.is_file():
        try:
            old_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            if old_manifest.get("pages") == manifest["pages"]:
                manifest_written = True
        except Exception:
            pass
    if not manifest_written:
        manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
        manifest_written = True

    if feed_written or manifest_written:
        log(f"Site data written: {len(feed['entries'])} feed entries, {len(manifest['pages'])} manifest pages", "FILE")


def seed_feed_from_mirror(state: dict):
    """One-time seed: populate feed.json and manifest.json from existing
    state data (pages, posts, media) so the site doesn't start empty."""
    feed_path = MIRROR_DIR / "docs" / "data" / "feed.json"
    if feed_path.is_file():
        try:
            existing = json.loads(feed_path.read_text(encoding="utf-8"))
            if existing.get("entries"):
                log("Feed already seeded — skipping", "FILE")
                return
        except Exception:
            pass

    log("Seeding feed from existing mirror data...", "FILE")
    seed_changes = []
    now = datetime.now(timezone.utc).isoformat()

    api = state.get("api", {})

    # Pages
    for item in api.get("/wp-json/wp/v2/pages", {}).get("items", []):
        seed_changes.append({
            "type": "api_items_added",
            "endpoint": "wp-json/wp/v2/pages",
            "detail": item.get("title", "Untitled page"),
            "items": [item],
        })

    # Posts
    for item in api.get("/wp-json/wp/v2/posts", {}).get("items", []):
        seed_changes.append({
            "type": "api_items_added",
            "endpoint": "wp-json/wp/v2/posts",
            "detail": item.get("title", "Untitled post"),
            "items": [item],
        })

    # Media
    for item in api.get("/wp-json/wp/v2/media", {}).get("items", []):
        seed_changes.append({
            "type": "media_orphan_upload",
            "endpoint": "media",
            "detail": item.get("title", f"Media #{item.get('id', '?')}"),
            "id": item.get("id"),
            "title": item.get("title", "Untitled media"),
            "url": item.get("link", ""),
            "author": item.get("author", 0),
        })

    log(f"Seeded {len(seed_changes)} initial changes ({sum(1 for c in seed_changes if c['type']=='api_items_added')} content items, {sum(1 for c in seed_changes if c['type']=='media_orphan_upload')} media)", "FILE")
    generate_page_data(state, seed_changes)

    # Add media items to manifest (they aren't handled by _update_manifest)
    import urllib.parse
    manifest_path = MIRROR_DIR / "docs" / "data" / "manifest.json"
    if manifest_path.is_file():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception:
            manifest = {"pages": []}
        existing_paths = {p["path"] for p in manifest["pages"]}
        added = 0
        for item in api.get("/wp-json/wp/v2/media", {}).get("items", []):
            path = urllib.parse.urlparse(item.get("link", "")).path
            if path and path not in existing_paths:
                manifest["pages"].append({
                    "path": path,
                    "title": item.get("title", path.split("/")[-1] or path),
                    "type": item.get("type", "attachment"),
                    "modified": item.get("modified", ""),
                    "date_gmt": item.get("date_gmt", ""),
                    "author": item.get("author", 0),
                })
                existing_paths.add(path)
                added += 1
        if added:
            # Resolve media author IDs to names
            media_user_map = _build_user_map(state)
            for p in manifest["pages"]:
                if p.get("type") == "attachment":
                    aid = p.get("author", 0)
                    p["author"] = media_user_map.get(aid, "") if aid else ""
            manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
            log(f"Added {added} media items to manifest", "FILE")


def git_push_site():
    """git add / commit / push the docs/ directory if anything changed."""
    import subprocess

    repo_dir = MIRROR_DIR
    site_dir = repo_dir / "docs"
    if not site_dir.is_dir():
        log("git push skipped — docs/ directory not found", "FILE")
        return

    try:
        # Stage all docs/ changes
        r = subprocess.run(
            ["git", "add", "--all", str(site_dir)],
            cwd=str(repo_dir), capture_output=True, text=True, timeout=30,
        )
        if r.returncode != 0:
            log(f"git add failed: {r.stderr.strip()}", "ERROR")
            return

        # Check if anything was staged
        r = subprocess.run(
            ["git", "diff", "--cached", "--quiet"],
            cwd=str(repo_dir), capture_output=True, timeout=30,
        )
        if r.returncode == 0:
            log("git push skipped — no site changes to commit", "FILE")
            return

        # Commit
        r = subprocess.run(
            ["git", "-c", f"user.name={GIT_USER_NAME}",
             "-c", f"user.email={GIT_USER_EMAIL}",
             "commit", "-m", "update site data [skip ci]"],
            cwd=str(repo_dir), capture_output=True, text=True, timeout=30,
        )
        if r.returncode != 0:
            log(f"git commit failed: {r.stderr.strip()}", "ERROR")
            return
        log(f"git commit: {r.stdout.strip()}", "FILE")

        # Push — embed token in remote URL if configured
        remote_url = f"https://x-access-token:{GITHUB_TOKEN}@github.com/vectorcmdr/project-skyscraper.git" if GITHUB_TOKEN else "origin"
        r = subprocess.run(
            ["git", "push", remote_url, GIT_BRANCH],
            cwd=str(repo_dir), capture_output=True, text=True, timeout=60,
        )
        if r.returncode != 0:
            log(f"git push failed: {r.stderr.strip()}", "ERROR")
            return
        log(f"git push: {r.stdout.strip()}", "FILE")

    except FileNotFoundError:
        log("git push skipped — git not found on PATH", "FILE")
    except subprocess.TimeoutExpired:
        log("git push timed out", "ERROR")
    except Exception as e:
        log(f"git push error: {e}", "ERROR")


def notify_changes(changes: list):
    """Dispatch notifications for detected changes (Discord + local files)."""

    urgent = {c["type"] for c in changes if c["type"] in (
        "sitemap_added", "sitemap_removed",
        "api_items_added", "api_items_removed",
        "page_content_changed", "media_replaced",
        "media_orphan_upload", "media_thumbnail_changed",
    )}

    # Write report file for each unique type
    by_type = defaultdict(list)
    for c in changes:
        by_type[c["type"]].append(c)

    for ctype, clist in by_type.items():
        write_report(ctype, {"count": len(clist), "changes": clist[:20]})

    # Ping the operator when any notifiable changes exist
    notified_types = {
        "sitemap_added", "sitemap_removed", "api_items_added",
        "api_items_removed", "api_items_modified", "page_content_changed",
        "media_replaced", "media_orphan_upload", "media_thumbnail_changed",
        "unpublished_detected",
    }
    # Discord: group notifications per category
    _embed_count = 0

    # Combined sitemap notification (URL adds/removes only)
    sitemap_changes = []
    for st in ("sitemap_added", "sitemap_removed"):
        if st in by_type:
            sitemap_changes.extend(by_type[st])

    if sitemap_changes:
        fields = []
        total_added = sum(c.get("count", 0) for c in by_type.get("sitemap_added", []))
        total_removed = sum(c.get("count", 0) for c in by_type.get("sitemap_removed", []))

        if total_added:
            c = by_type["sitemap_added"][0]
            fields.append({"name": f"Added ({total_added})", "value": "\n".join(c["urls"][:15])[:1024]})
        if total_removed:
            c = by_type["sitemap_removed"][0]
            fields.append({"name": f"Removed ({total_removed})", "value": "\n".join(c["urls"][:15])[:1024]})

        desc_parts = []
        if total_added:
            desc_parts.append(f"+{total_added}")
        if total_removed:
            desc_parts.append(f"-{total_removed}")

        send_discord(
            title=f"Sitemap Changed: {' '.join(desc_parts)}",
            description=", ".join(desc_parts),
            fields=fields[:10] or None,
            color=0x00ff88,
        )

    if "api_items_added" in by_type:
        clist = by_type["api_items_added"]
        total = sum(c["count"] for c in clist)
        desc_parts = [f"{c['count']} in {c['endpoint'].split('/')[-1]}" for c in clist]
        fields = []
        for c in clist:
            items_str = "\n".join(
                f"#{i['id']}: {i.get('title', '(untitled)')}"
                for i in c["items"][:10]
            )
            if items_str:
                fields.append({"name": c["endpoint"].split("/")[-1], "value": items_str[:1024]})
        send_discord(
            title=f"New API Items: {total}",
            description=", ".join(desc_parts),
            fields=fields[:10] or None,
            color=0x00aaff,
        )

    if "api_items_removed" in by_type:
        clist = by_type["api_items_removed"]
        total = sum(c["count"] for c in clist)
        desc_parts = [f"{c['count']} in {c['endpoint'].split('/')[-1]}" for c in clist]
        fields = []
        for c in clist:
            ids_str = ", ".join(str(i) for i in c["ids"][:20])
            if ids_str:
                fields.append({"name": c["endpoint"].split("/")[-1], "value": ids_str[:1024]})
        send_discord(
            title=f"Removed API Items: {total}",
            description=", ".join(desc_parts),
            fields=fields[:10] or None,
            color=0xff4444,
        )

    if "api_items_modified" in by_type:
        clist = by_type["api_items_modified"]
        total = sum(c["count"] for c in clist)
        desc_parts = [f"{c['count']} in {c['endpoint'].split('/')[-1]}" for c in clist]
        fields = []
        for c in clist:
            ep_label = c["endpoint"].split("/")[-1]
            items_str = "\n".join(
                f"#{i['id']}: {i.get('title', '(untitled)')}"
                for i in c["items"][:10]
            )
            if items_str:
                fields.append({"name": ep_label, "value": items_str[:1024]})
            for d in (c.get("diffs") or [])[:3]:
                diff_text = _strip_html(d["diff"])
                short_url = d["url"].split("/")[-1]
                fields.append({
                    "name": f"Diff: {short_url}",
                    "value": f"```diff\n{diff_text}\n```" if len(diff_text) < 950 else diff_text,
                })
        send_discord(
            title=f"Modified API Items: {total}",
            description=", ".join(desc_parts),
            fields=fields[:10] or None,
            color=0xffaa00,
        )

    if "page_content_changed" in by_type:
        clist = by_type["page_content_changed"]
        fields = []
        for c in clist[:5]:
            page_label = c["url"].split("/")[-1] or c["url"]
            diff_text = ""
            if c.get("diffs"):
                for d in c["diffs"][:1]:
                    diff_text = _strip_html(d["diff"])
            val = page_label[:200]
            if diff_text:
                val += f"\n```diff\n{diff_text[:900]}\n```"
            fields.append({"name": "Page Changed", "value": val[:1024]})
        send_discord(
            title=f"Page Content Changed: {len(clist)} page(s)",
            description="Content hash changed for " + ", ".join(c["url"] for c in clist[:3]),
            fields=fields[:10] or None,
            color=0xff8800,
        )

    if "media_replaced" in by_type:
        clist = by_type["media_replaced"]
        fields = []
        for c in clist[:5]:
            fields.append({
                "name": f"Media #{c['id']}",
                "value": f"Old: {c['old_url'][:200]}\nNew: {c['new_url'][:200]}",
            })
        send_discord(
            title=f"Media Files Replaced: {len(clist)}",
            description=f"{len(clist)} media file(s) changed",
            fields=fields[:10] or None,
            color=0xff00ff,
        )

    if "media_thumbnail_changed" in by_type:
        clist = by_type["media_thumbnail_changed"]
        fields = []
        for c in clist[:10]:
            fields.append({
                "name": f"Media #{c['id']} {c['size']}",
                "value": c["url"][:200],
            })
        send_discord(
            title=f"Media Thumbnails Changed: {len(clist)}",
            description=f"{len(clist)} thumbnail variant(s) changed",
            fields=fields[:10] or None,
            color=0x88aaff,
        )

    if "media_orphan_upload" in by_type:
        c_list = by_type["media_orphan_upload"]
        send_discord(
            title=f"Orphan Media Uploads: {len(c_list)}",
            description=f"{len(c_list)} unattached media file(s) detected",
            fields=[{"name": "Files", "value": "\n".join(
                f"#{c['id']}: {c.get('title', '(untitled)')}" for c in c_list[:10]
            )[:1024]}],
            color=0xff00aa,
        )

    if "unpublished_detected" in by_type:
        c_list = by_type["unpublished_detected"]
        send_discord(
            title=f"Unpublished Content: {len(c_list)}",
            description=f"{len(c_list)} unpublished item(s) detected via API probe",
            fields=[{"name": "Items", "value": "\n".join(
                f"#{c['id']} ({c['endpoint']}) HTTP {c['status']}" for c in c_list[:10]
            )[:1024]}],
            color=0xaa44ff,
        )

    # Summary notification only for non-notified types
    if not any(t in by_type for t in notified_types):
        non_silent = set(by_type.keys())
        if non_silent:
            total = sum(len(c) for t, c in by_type.items())
            parts = [f"{t}={len(c)}" for t, c in by_type.items()]
            send_discord_simple(f"Monitor detected {total} change(s): " + "; ".join(parts))


# ---------------------------------------------------------------------------
# Daemon loop
# ---------------------------------------------------------------------------

def print_banner():
    print(flush=True)
    print("  project-skyscraper.com - Change Monitor", flush=True)
    print(f"  {MIRROR_DIR}", flush=True)
    print(f"  Intervals: fast={POLL_INTERVALS['fast']}s  "
          f"medium={POLL_INTERVALS['medium']}s  "
          f"deep={POLL_INTERVALS['deep']}s", flush=True)
    print(f"  Discord: {'enabled' if DISCORD_WEBHOOK else 'disabled'}", flush=True)
    print(flush=True)
    print("  Press Ctrl+C to stop", flush=True)
    print(flush=True)


def daemon_loop(state: dict, quiet: bool = False):
    """Main polling loop. Runs until interrupted."""
    def _log(msg, level="INFO"):
        if not quiet and level in ("FAST", "MEDIUM", "DEEP", "CHECK"):
            return  # Suppress routine messages in quiet mode
        log(msg, level)

    _log = log  # For now, use full logging

    last_tiers = {}
    last_tiers["fast"] = 0
    last_tiers["medium"] = 0
    last_tiers["deep"] = 0

    # Run an initial full check cycle
    _log("Starting initial check cycle...")
    run_check_cycle(state, tiers={"fast", "medium", "deep"})
    _log("Initial check complete")

    while True:
        now = time.time()

        # Determine which tiers to run
        tiers_to_run = set()

        if now - last_tiers["fast"] >= POLL_INTERVALS["fast"]:
            tiers_to_run.add("fast")
            last_tiers["fast"] = now

        if now - last_tiers["medium"] >= POLL_INTERVALS["medium"]:
            tiers_to_run.add("medium")
            last_tiers["medium"] = now

        if now - last_tiers["deep"] >= POLL_INTERVALS["deep"]:
            tiers_to_run.add("deep")
            last_tiers["deep"] = now

        if tiers_to_run:
            try:
                run_check_cycle(state, tiers=tiers_to_run)
            except Exception as e:
                log(f"Check cycle failed: {e}", "ERROR")
                import traceback
                log(traceback.format_exc(), "ERROR")

        # Clean up old report files periodically
        if now % 3600 < 1:
            cleanup_old_reports()

        time.sleep(1)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def main():
    _ensure_report_dir()

    # Parse args
    args = set(sys.argv[1:])
    single_check = "--check" in args or "-c" in args
    quiet = "--quiet" in args or "-q" in args

    if not acquire_lock():
        log("Cannot acquire lock. If no other instance is running, manually remove: "
            f"{LOCK_FILE}", "ERROR")
        sys.exit(1)

    print_banner()
    state = load_state()
    save_state(state)  # Ensure state directory/file exists
    seed_feed_from_mirror(state)

    try:
        if single_check:
            log("Single check mode")
            run_check_cycle(state, tiers={"fast", "medium", "deep"})
            log("Check complete")
        else:
            daemon_loop(state, quiet=quiet)
    except KeyboardInterrupt:
        log("Shutting down (Ctrl+C)")
    finally:
        save_state(state)
        release_lock()
        log("Monitor stopped")


if __name__ == "__main__":
    main()
