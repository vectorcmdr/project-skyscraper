"""Configuration loader -- reads config.json, provides defaults."""

import json
from pathlib import Path

_CONFIG = {}
_config_path = Path(__file__).parent.parent / "config.json"
if _config_path.is_file():
    try:
        _CONFIG = json.loads(_config_path.read_text(encoding="utf-8"))
    except Exception:
        _CONFIG = {}

BASE_URL = _CONFIG.get("base_url", "https://project-skyscraper.com")
MIRROR_DIR = Path(__file__).parent.parent.resolve()
STATE_DIR = MIRROR_DIR / "state"
STATE_FILE = STATE_DIR / "monitor_state.json"
REPORT_DIR = MIRROR_DIR / "monitor_reports"
LOCK_FILE = STATE_DIR / ".monitor.lock"
LOG_FILE = REPORT_DIR / "monitor.log"
DIFF_DIR = MIRROR_DIR / "diffs"
SITE_DIR = MIRROR_DIR / "docs"
DATA_DIR = SITE_DIR / "data"

USER_AGENT = _CONFIG.get("user_agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 project-skyscraper-monitor/1.0")

DISCORD_WEBHOOK = _CONFIG.get("discord_webhook", "")
DISCORD_PING_ID = _CONFIG.get("discord_ping_id", "")

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

IGNORE_HOSTS = {"i0.wp.com", "fonts.wp.com", "s0.wp.com", "stats.wp.com", "c0.wp.com"}

TRACE_DISCOURSE_URL = "https://forums.atlas-65.com/u/the_architect.json"
TRACE_STATUS_FILE = MIRROR_DIR / "docs" / "status" / "trace.json"
TRACE_ACTIVE_THRESHOLD = 300
TRACE_POLL_INTERVAL = 60

PROBE_RANGE = 300
PROBE_CHUNK_SIZE = 30
PAGE_CHECK_CHUNK = 15
MAX_WORKERS = 8
MAX_MEDIA_WORKERS = 3
FETCH_TIMEOUT = 15
HEAD_TIMEOUT = 10
DIFF_MAX_LINES = 30

PASSWORD_PROTECTED_PAGES = {
    "https://project-skyscraper.com/request-memory-timestamp-094317/": "EMILY",
    "https://project-skyscraper.com/2026/05/31/sec-log-193727/": "EMILY",
    "https://project-skyscraper.com/report-bru-ent-reunion-peak/": "EVENT HORIZON",
}

MIRROR_SUBDIRS = ["html", "api", "media", "assets", "discovery", "extras", "endpoints", "third_party"]

BINARY_EXTENSIONS = frozenset({
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".svg",
    ".woff2", ".woff", ".ttf", ".eot", ".otf",
    ".zip", ".gz", ".pdf", ".mp4", ".webm", ".mp3",
})

MEANINGFUL_CHANGE_TYPES = frozenset({
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
})
