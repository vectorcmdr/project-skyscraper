"""Configuration loader -- env vars first, then config.json, then defaults."""

import json
import os
from pathlib import Path

_CONFIG = {}
_config_path = Path(__file__).parent.parent / "config.json"
if _config_path.is_file():
    try:
        _CONFIG = json.loads(_config_path.read_text(encoding="utf-8"))
    except Exception:
        _CONFIG = {}


def _setting(key: str, default=""):
    """Resolve a setting: environment variable PS_<KEY> first, then
    config.json, then the default. Hosted runs (GitHub Actions) provide
    secrets via env; local runs keep using config.json."""
    env_key = "PS_" + key.upper()
    if env_key in os.environ:
        return os.environ[env_key]
    return _CONFIG.get(key, default)


# True when running on ephemeral hosted infrastructure (GitHub Actions).
# Toggles: reduced snapshot retention, disabled recalldreams full mirror,
# extended commit scope in git_pusher.
HOSTED = os.environ.get("PS_HOSTED", "").strip().lower() in ("1", "true", "yes")

BASE_URL = _setting("base_url", "https://project-skyscraper.com")
MIRROR_DIR = Path(__file__).parent.parent.resolve()
STATE_DIR = MIRROR_DIR / "state"
STATE_FILE = STATE_DIR / "monitor_state.json"
REPORT_DIR = MIRROR_DIR / "monitor_reports"
LOCK_FILE = STATE_DIR / ".monitor.lock"
LOG_FILE = REPORT_DIR / "monitor.log"
DIFF_DIR = MIRROR_DIR / "diffs"
SITE_DIR = MIRROR_DIR / "docs"
DATA_DIR = SITE_DIR / "data"

USER_AGENT = _setting("user_agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 project-skyscraper-monitor/1.0")

DISCORD_WEBHOOK = _setting("discord_webhook", "")
DISCORD_PING_ID = _setting("discord_ping_id", "")

# Extra Discord webhooks (secondary servers). The primary webhook
# (DISCORD_WEBHOOK) is always first; the extras receive the same embeds.
_extra_webhooks = _CONFIG.get("discord_webhooks", [])
if isinstance(_extra_webhooks, str):
    _extra_webhooks = [_extra_webhooks]
# Dedupe while preserving order: a duplicated webhook URL would otherwise
# post the same embed multiple times to one server.
DISCORD_WEBHOOKS = []
for _w in [DISCORD_WEBHOOK] + list(_extra_webhooks):
    if _w and _w not in DISCORD_WEBHOOKS:
        DISCORD_WEBHOOKS.append(_w)

GIT_BRANCH = _setting("git_branch", "main")
GIT_USER_NAME = _setting("git_user_name", "Project Skyscraper Monitor")
GIT_USER_EMAIL = _setting("git_user_email", "monitor@project-skyscraper.com")
GITHUB_TOKEN = _setting("github_token", "")

POLL_INTERVALS = {
    "fast": 30,
    "medium": 60,
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
TRACE_ACTIVE_THRESHOLD = 1200  # 2x the hosted 10-min poll interval
TRACE_POLL_INTERVAL = 60

PROBE_RANGE = 300
PROBE_CHUNK_SIZE = 30
PAGE_CHECK_CHUNK = 15
MAX_WORKERS = 8
MAX_MEDIA_WORKERS = 3
FETCH_TIMEOUT = 15
HEAD_TIMEOUT = 10
DIFF_MAX_LINES = 30

STALE_BYPASS_URLS = frozenset({
    "/counting/",
    "/neural-network-status/",
})

# Collection snapshots: full raw collection responses saved when items change,
# so bulk content-only waves (memory bloc shifts) can be diffed offline even
# if the live site reverts before per-item fetches run. Hosted runs keep only
# the newest pair per endpoint (they are committed to git; 20 per endpoint
# would bloat the repo).
COLLECTION_SNAPSHOT_DIR = REPORT_DIR / "collections"
SNAPSHOT_KEEP = 2 if HOSTED else 20

# Memory-bloc wave quiescence (see MEMORY_BLOC_WAVE_QUIESCENCE_PLAN.md).
# When WAVE_SILENT_NORMAL is true, the daemon suppresses Discord embeds and
# git pushes for in-pattern cycle types (normal_round_trip, normal_dribble,
# trivial) and surfaces only anomalies, page changes, and the periodic
# roll-up. The classification log (state/wave_classification.jsonl) always
# records every cycle's classification regardless of suppression.
WAVE_SILENT_NORMAL = True
WAVE_ROLLUP_N_CYCLES = 6      # emit roll-up after this many normal cycles
WAVE_ROLLUP_HOURS = 0.5       # or after this many hours since last roll-up
MULTIPLIER = 1.0016            # canonical memory-bloc shift multiplier
MULTIPLIER_TOLERANCE = 1e-6    # relative tolerance for the multiplier match

PASSWORD_PROTECTED_PAGES = {
    "https://project-skyscraper.com/request-memory-timestamp-094317/": "EMILY",
    "https://project-skyscraper.com/2026/05/31/sec-log-193727/": "EMILY",
    "https://project-skyscraper.com/report-bru-ent-reunion-peak/": "EVENT HORIZON",
    "https://project-skyscraper.com/flow/": "ComputerZeroTime",
}

MIRROR_SUBDIRS = ["html", "api", "media", "assets", "discovery", "extras", "endpoints", "third_party"]

BINARY_EXTENSIONS = frozenset({
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".svg",
    ".woff2", ".woff", ".ttf", ".eot", ".otf",
    ".zip", ".gz", ".pdf", ".mp4", ".webm", ".mp3",
})

EXTERNAL_SITES = {
    "wakingtitan.com": {
        "url": "https://wakingtitan.com",
        "type": "generic",
        "label": "WAKING TITAN",
    },
    "recalldreams.dev": {
        "url": "https://recalldreams.dev",
        "type": "wordpress",
        "label": "RECALL DREAMS",
    },
    "freeimage.host": {
        "url": "https://freeimage.host/skyscraper_prj",
        "type": "generic",
        "label": "freeimage",
    },
}

EXTERNAL_CHECK_INTERVAL = 7200  # every 2 hours
EXTERNAL_LONG_POLL_INTERVAL = 43200  # DNS deep check every 12h
RECALLDREAMS_MIRROR_INTERVAL = 21600  # full mirror every 6h
EXTERNAL_WP_PAGES_INTERVAL = 600  # external WP page HTML checks every 10 min
EXTERNAL_NOTIFY_DEDUP_WINDOW = 86400  # 24h: suppress repeat external content-change notifications for an already-notified content signature

# YouTube channel upload tracking (No Man's Sky / Hello Games).
# All three sub-checks run on the fast tier and are gated by state
# timestamps, so the configured intervals throttle each sub-check
# independently of the daemon loop: new public videos and hidden uploads
# via RSS/playlist, plus the public count from the About page.
YOUTUBE_SITES = {
    "hellogames": {
        "channel_id": "UCGKx5XhGuf09VERiw_QIemA",
        "url": "https://www.youtube.com/@HelloGamesTube",
        "label": "HELLO GAMES YT",
    },
}
YOUTUBE_RSS_INTERVAL = 300          # public uploads via RSS feed (30s hammering triggered stale cached feeds)
YOUTUBE_PLAYLIST_INTERVAL = 300     # total count + hidden uploads via uploads playlist
YOUTUBE_COUNTS_INTERVAL = 1800      # public count via About page (not time-critical)
YOUTUBE_CHANNEL_BROWSE_BASE = "https://www.youtube.com/youtubei/v1/browse?prettyPrint=false"

MEANINGFUL_CHANGE_TYPES = frozenset({
    "api_items_added",
    "api_items_modified",
    "api_items_removed",
    "database_entry_changed",
    "external_content_changed",
    "external_dns_changed",
    "external_robots_txt_changed",
    "external_sitemap_changed",
    "external_unpublished_detected",
    "external_unpublished_detected",
    "external_youtube_video_public",
    "external_youtube_video_hidden",
    "external_youtube_video_removed",
    "external_youtube_count_changed",
    "media_orphan_upload",
    "media_replaced",
    "media_thumbnail_changed",
    "page_content_changed",
    "sitemap_added",
    "sitemap_removed",
    "unpublished_detected",
})
