#!/usr/bin/env python3
"""
project-skyscraper.com - Complete Mirror Update Script
-vector_cmdr
======================================================
Fully self-discovering. No hardcoded IDs, no hardcoded URL lists.
Run:  python update_mirror.py

Re-run anytime to check for updates. Diffs saved to diffs/.
Change history accumulated in diffs/CHANGELOG.md.
"""

import difflib
import hashlib
import json
import os
import re
import time
import threading
import urllib.parse
import urllib.request
import urllib.error
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

import jsbeautifier

BASE_URL = "https://project-skyscraper.com"
MIRROR_DIR = Path(__file__).parent.resolve()
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 project-skyscraper-mirror/1.0"

stats = {"fetched": 0, "skipped": 0, "failed": 0, "changed": 0, "new": 0}
changes = []


def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")


def _save_diff(url: str, path: Path, old_bytes: bytes, new_bytes: bytes, binary: bool = False):
    """Generate unified diff between old and new content, save to diffs/."""
    diff_dir = MIRROR_DIR / "diffs"
    diff_dir.mkdir(parents=True, exist_ok=True)
    rel = str(path.relative_to(MIRROR_DIR)).replace("\\", "/")
    safe_name = re.sub(r'[^a-zA-Z0-9_\-.]', '_', rel) + ".diff"

    if binary:
        header = [
            f"# Diff: {url}",
            f"# File: {rel}",
            f"# Timestamp: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}",
            f"# Binary: size changed",
            "",
            f"--- old/{rel}",
            f"+++ new/{rel}",
            f"-{_fmt_size(len(old_bytes))}",
            f"+{_fmt_size(len(new_bytes))}",
        ]
        diff_path = diff_dir / f"{safe_name}"
        diff_path.write_text("\n".join(header) + "\n", encoding="utf-8")
        log(f"    DIFF saved: {safe_name} (size change)")
        return

    try:
        old_text = old_bytes.decode("utf-8", errors="replace")
        new_text = new_bytes.decode("utf-8", errors="replace")
    except Exception:
        return
    old_text = _beautify_content(old_text, path)
    new_text = _beautify_content(new_text, path)
    old_lines = old_text.splitlines(keepends=True)
    new_lines = new_text.splitlines(keepends=True)
    diff_lines = list(difflib.unified_diff(
        old_lines, new_lines,
        fromfile=f"old/{rel}", tofile=f"new/{rel}",
        n=3
    ))
    if not diff_lines:
        return
    if not _diff_has_real_changes("".join(diff_lines)):
        return
    header = [
        f"# Diff: {url}",
        f"# File: {rel}",
        f"# Timestamp: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}",
        f"# Lines: {len(old_lines)} old -> {len(new_lines)} new",
        "# Beautified: yes",
        "",
    ]
    diff_path = diff_dir / f"{safe_name}"
    diff_path.write_text("\n".join(header) + "".join(diff_lines), encoding="utf-8")
    log(f"    DIFF saved: {safe_name}")


def _fmt_size(bytes_val):
    for unit in ("B", "KB", "MB", "GB"):
        if bytes_val < 1024:
            return f"{bytes_val:.1f} {unit}"
        bytes_val /= 1024
    return f"{bytes_val:.1f} TB"


def _hash_bytes(data):
    return hashlib.md5(data).hexdigest()


def _beautify_content(text: str, path: Path) -> str:
    """Beautify minified content before diffing to avoid massive single-line diffs."""
    ext = path.suffix.lower()
    if ext == '.json':
        try:
            obj = json.loads(text)
            return json.dumps(obj, indent=2, ensure_ascii=False) + "\n"
        except (json.JSONDecodeError, ValueError):
            pass
    if ext == '.js' or ext == '.mjs':
        try:
            return jsbeautifier.beautify(text) + "\n"
        except Exception:
            pass
    if ext == '.html':
        try:
            return _beautify_html_scripts(text)
        except Exception:
            pass
    return text


def _beautify_html_scripts(html: str) -> str:
    """Find <script> blocks in HTML and beautify their JS content."""
    def _replacer(m):
        attrs = (m.group(1) or "").strip()
        content = m.group(2) or ""
        if not content.strip():
            return m.group(0)
        attrs_lower = attrs.lower()
        if re.search(r'\bsrc\s*=', attrs_lower):
            return m.group(0)
        if 'application/json' in attrs_lower or 'application/ld+json' in attrs_lower:
            return m.group(0)
        try:
            beautified = jsbeautifier.beautify(content)
            return f'<script {attrs}>{beautified}</script>' if attrs else f'<script>{beautified}</script>'
        except Exception:
            return m.group(0)
    return re.sub(
        r'<script([^>]*?)>([\s\S]*?)</script>',
        _replacer,
        html,
        flags=re.IGNORECASE
    )


_NOISE_RE = [
    re.compile(r'^[ +-]\tgenerated in \d+\.\d+ seconds$'),
    re.compile(r'^[ +-]\t\d+ bytes batcached for \d+ seconds$'),
    re.compile(r'^[ +-]\tgenerated \d+ seconds ago$'),
    re.compile(r'^[ +-]\tserved from batcache in \d+\.\d+ seconds$'),
    re.compile(r'^[ +-]\texpires in \d+ seconds$'),
    re.compile(r'^[ +-]<!--$'),
    re.compile(r'^[ +-]-->$'),
]

def _is_noise_line(line: str) -> bool:
    return any(r.match(line) for r in _NOISE_RE)

def _filter_noise_diff_lines(lines: list) -> list:
    return [l for l in lines if not _is_noise_line(l)]


# --- URL -> path mapping ---

def url_to_path(url: str, subdir: str = "") -> Path:
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


# --- HTTP fetch with caching ---

def fetch(url: str, subdir: str = "", binary: bool = False,
           headers_extra: dict = None, save_headers: bool = False):
    path = url_to_path(url, subdir=subdir)
    path.parent.mkdir(parents=True, exist_ok=True)

    old_bytes = path.read_bytes() if path.is_file() else None
    old_hash = _hash_bytes(old_bytes) if old_bytes is not None else None

    req = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT, "Accept": "*/*", **(headers_extra or {}),
    })
    try:
        resp = urllib.request.urlopen(req, timeout=30)
        content = resp.read()
        code = resp.status
    except urllib.error.HTTPError as e:
        stats["failed"] += 1
        try:
            content = e.read()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
            log(f"  ERR  {url} -> {e.code}")
        except Exception:
            pass
        return ("error", path, e.code)
    except Exception as e:
        stats["failed"] += 1
        log(f"  FAIL {url} -> {e}")
        return ("error", path, 0)

    new_hash = _hash_bytes(content)
    if old_hash == new_hash:
        stats["skipped"] += 1
        return ("skipped", path, code, content)

    if old_hash is not None:
        _save_diff(url, path, old_bytes, content, binary)

    path.write_bytes(content)
    if old_hash is None:
        stats["new"] += 1
        log(f"  NEW  {url}")
    else:
        stats["changed"] += 1
        log(f"  CHG  {url}")
        changes.append((url, path))
    stats["fetched"] += 1

    if save_headers:
        hdr = path.parent / (path.name + ".headers.json")
        hdr.write_text(json.dumps(dict(resp.headers.items()), indent=2, default=str))
    return ("ok", path, code, content)


_probe_lock = threading.Lock()


def _probe_fetch(url, subdir=""):
    """Thread-safe fetch for probe phase - minimal side effects, locked stats."""
    path = url_to_path(url, subdir=subdir)
    path.parent.mkdir(parents=True, exist_ok=True)

    old_bytes = path.read_bytes() if path.is_file() else None
    old_hash = _hash_bytes(old_bytes) if old_bytes is not None else None

    req = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT, "Accept": "*/*",
    })
    try:
        resp = urllib.request.urlopen(req, timeout=30)
        content = resp.read()
        code = resp.status
    except urllib.error.HTTPError as e:
        code = e.code
        content = e.read()

    new_hash = _hash_bytes(content)
    if old_hash and old_hash == new_hash:
        with _probe_lock:
            stats["skipped"] += 1
        return ("skipped", path, code, content)

    if old_hash is not None:
        with _probe_lock:
            _save_diff(url, path, old_bytes, content)

    path.write_bytes(content)
    with _probe_lock:
        if old_hash is None:
            stats["new"] += 1
        else:
            stats["changed"] += 1
        stats["fetched"] += 1

    return ("ok" if code < 400 else "error", path, code, content)


def _json_fetch(endpoint: str):
    """Fetch JSON endpoint, return parsed data or None."""
    url = f"{BASE_URL}{endpoint}"
    result = fetch(url, subdir="api")
    if result[0] == "ok":
        try:
            return json.loads(result[3])
        except (json.JSONDecodeError, IndexError):
            pass
    return None


# --- Cookie/Tools for password-unlock ---

PAGE_PASSWORD = "EMILY"

def _get_postpass_cookie() -> dict:
    """POST password to wp-login.php?action=postpass, return {cookie_name: raw_value}.

    Uses a custom opener that does NOT follow redirects so we can capture
    the Set-Cookie header from the 302 response.
    """
    url = f"{BASE_URL}/wp-login.php?action=postpass"
    data = urllib.parse.urlencode({
        "post_password": PAGE_PASSWORD,
        "Submit": "Enter",
        "redirect_to": f"{BASE_URL}/request-memory-timestamp-094317/",
    }).encode()

    # Custom handler to suppress redirect following
    class NoRedirectHandler(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None  # Don't follow

    opener = urllib.request.build_opener(NoRedirectHandler)
    req = urllib.request.Request(url, data=data, headers={
        "User-Agent": USER_AGENT,
        "Content-Type": "application/x-www-form-urlencoded",
    })
    try:
        resp = opener.open(req, timeout=30)
    except urllib.error.HTTPError as e:
        resp = e

    # Collect all Set-Cookie headers
    cookies = {}
    # urllib.response returns all headers with the same key joined by ', '
    raw = resp.headers.get_all("Set-Cookie") if hasattr(resp.headers, "get_all") else None
    if raw is None:
        # Fallback: split by common delimiter patterns
        set_cookie = resp.headers.get("Set-Cookie", "")
        raw = re.split(r', (?=[a-zA-Z0-9_\-]+=)', set_cookie) if set_cookie else []
    for part in raw:
        part = part.strip()
        m = re.search(r'(wp-postpass_[a-f0-9]+)=([^;]+)', part)
        if m:
            cooked = urllib.parse.unquote(m.group(2))
            cookies[m.group(1)] = cooked
            log(f"  Got postpass cookie: {m.group(1)}")
    if not cookies:
        # Debug: dump all Set-Cookie headers
        all_headers = dict(resp.headers.items())
        log(f"  WARN: No postpass cookie found. Headers: {json.dumps(all_headers, default=str)[:500]}")
    return cookies


def fetch_unlocked(url: str, subdir: str = "", binary: bool = False,
                   headers_extra: dict = None) -> tuple:
    """Fetch a password-protected page by first obtaining a postpass cookie."""
    cookies = _get_postpass_cookie()
    if not cookies:
        log(f"  WARN: No postpass cookie obtained for {url}")
        return ("error", None, 0, b"")

    path = url_to_path(url, subdir=subdir)
    path.parent.mkdir(parents=True, exist_ok=True)
    old_bytes = path.read_bytes() if path.is_file() else None
    old_hash = _hash_bytes(old_bytes) if old_bytes is not None else None

    # Build cookie header
    cookie_parts = [f"{k}={v}" for k, v in cookies.items()]
    cookie_header = "; ".join(cookie_parts)

    req = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT,
        "Accept": "*/*",
        "Cookie": cookie_header,
        **(headers_extra or {}),
    })
    try:
        resp = urllib.request.urlopen(req, timeout=30)
        content = resp.read()
        code = resp.status
    except urllib.error.HTTPError as e:
        stats["failed"] += 1
        try:
            content = e.read()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
            log(f"  ERR  {url} -> {e.code}")
        except Exception:
            pass
        return ("error", path, e.code, b"")
    except Exception as e:
        stats["failed"] += 1
        log(f"  FAIL {url} -> {e}")
        return ("error", path, 0, b"")

    # Check if we got the password form back (still protected)
    text = content.decode("utf-8", errors="replace")
    if "post-password-form" in text or "This content is password-protected" in text:
        log(f"  STILL LOCKED after password cookie: {url}")
        if old_hash is None:
            path.write_bytes(content)
            stats["new"] += 1
        return ("locked", path, code, content)

    new_hash = _hash_bytes(content)
    if old_hash == new_hash:
        stats["skipped"] += 1
        return ("skipped", path, code, content)

    if old_hash is not None:
        _save_diff(url, path, old_bytes, content, binary)

    path.write_bytes(content)
    if old_hash is None:
        stats["new"] += 1
        log(f"  NEW (unlocked) {url}")
    else:
        stats["changed"] += 1
        log(f"  CHG (unlocked) {url}")
        changes.append((url, path))
    stats["fetched"] += 1
    return ("ok", path, code, content)


PASSWORD_PROTECTED_PAGES = [
    "https://project-skyscraper.com/request-memory-timestamp-094317/",
    "https://project-skyscraper.com/2026/05/31/sec-log-193727/",  # discovered but never fetched
]


def fetch_password_protected_pages():
    """Fetch pages that require the EMILY password."""
    log("=== FETCHING PASSWORD-PROTECTED PAGES ===")
    for url in PASSWORD_PROTECTED_PAGES:
        fetch_unlocked(url, subdir="html")
        time.sleep(0.5)


# --- Phase 1: Discovery ---

def discover_sitemap_urls() -> dict:
    """Fetch sitemap index, sub-sitemaps, return dict of {url: type}."""
    urls = {}
    sitemap_index = f"{BASE_URL}/sitemap.xml"
    r = fetch(sitemap_index, subdir="discovery")
    if r[0] == "ok":
        try:
            tree = ET.parse(str(r[1]))
            root = tree.getroot()
            ns = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
            subs = [e.text for e in root.findall(".//sm:sitemap/sm:loc", ns) if e.text]
            for sub in subs:
                fetch(sub, subdir="discovery")
                sr = fetch(sub, subdir="discovery")
                if sr[0] != "error":
                    try:
                        st = ET.parse(str(sr[1]))
                        sroot = st.getroot()
                        for e in sroot.findall(".//sm:url/sm:loc", ns):
                            if e.text:
                                urls[e.text] = "page"
                        img_ns = {**ns, "image": "http://www.google.com/schemas/sitemap-image/1.1"}
                        for img in sroot.findall(".//sm:url/image:image/image:loc", img_ns):
                            if img.text:
                                urls[img.text] = "image"
                    except ET.ParseError:
                        pass
        except ET.ParseError:
            pass
    # Fallback: try known sub-sitemap paths
    if not urls:
        for alt in ["/sitemap-1.xml", "/image-sitemap-1.xml", "/news-sitemap.xml"]:
            sr = fetch(f"{BASE_URL}{alt}", subdir="discovery")
            if sr[0] != "error":
                try:
                    st = ET.parse(str(sr[1]))
                    sroot = st.getroot()
                    ns = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
                    for e in sroot.findall(".//sm:url/sm:loc", ns):
                        if e.text:
                            urls[e.text] = "page"
                except ET.ParseError:
                    pass
    return urls


def discover_rest_api():
    """Fetch root namespace index and return list of namespace routes."""
    data = _json_fetch("/wp-json/")
    namespaces = []
    if data and isinstance(data, dict):
        ns_list = data.get("namespaces", [])
        routes = data.get("routes", {})
        for ns in ns_list:
            namespaces.append(f"/wp-json/{ns}")
        # Also return route keys for probing
        route_keys = list(routes.keys()) if isinstance(routes, dict) else []
        return namespaces, route_keys
    return [], []


def discover_list(endpoint: str) -> list:
    """Fetch a list endpoint (posts, pages, media) and return all items."""
    items = []
    # Try with higher per_page first
    for url_suffix in [f"{endpoint}?per_page=100", f"{endpoint}?per_page=50", endpoint]:
        data = _json_fetch(url_suffix)
        if isinstance(data, list):
            items = data
            break
    if not items:
        # Try single-item fetch
        data = _json_fetch(endpoint)
        if isinstance(data, list):
            items = data
    return items


def extract_html_resource_urls(subdir: str = "html") -> set:
    """Scan all saved HTML pages for any wp-content URL, external href/src, etc."""
    found = set()
    html_dir = MIRROR_DIR / subdir
    if not html_dir.exists():
        return found
    # Patterns for URLs in HTML
    patterns = [
        re.compile(r'''(?:src|href|data-src|content)="([^"]+)"'''),
        re.compile(r'''url\(['"]?([^'")\s]+)['"]?\)'''),
    ]
    for hf in sorted(html_dir.glob("*.html")):
        text = hf.read_text(encoding="utf-8", errors="replace")
        for pat in patterns:
            for m in pat.finditer(text):
                u = m.group(1)
                if u.startswith("//"):
                    u = "https:" + u
                if u.startswith(("http://", "https://")) or u.startswith("/"):
                    found.add(u)
    return found


# --- Phase 2: HTML pages ---

def fetch_html_pages(sitemap_urls: dict):
    log("=== FETCHING HTML PAGES ===")
    for url in sitemap_urls:
        if url.startswith(BASE_URL) and sitemap_urls[url] == "page":
            fetch(url, subdir="html", save_headers=True)
            time.sleep(0.3)
    # Always fetch root
    fetch(BASE_URL, subdir="html", save_headers=True)


# --- Phase 3: REST API ---

def fetch_api_endpoints():
    log("=== FETCHING REST API ENDPOINTS ===")

    # 1) Root + route discovery
    log("  --- Root & Route Discovery ---")
    namespaces, route_keys = discover_rest_api()

    # 2) Fetch all known namespaced roots
    # Note: oembed/1.0 and wp/v2 are excluded because they contain
    # sub-endpoints that must live under a directory of the same name.
    known_ns_roots = {
        "/wp-json/jetpack/v4", "/wp-json/wpcom/v2", "/wp-json/wpcom/v3",
        "/wp-json/wpcomsh/v1", "/wp-json/code-snippets/v1",
        "/wp-json/crowdsignal-forms/v1", "/wp-json/wp-statistics/v2",
        "/wp-json/wp-site-health/v1", "/wp-json/wp-abilities/v1",
        "/wp-json/akismet/v1", "/wp-json/my-jetpack/v1",
        "/wp-json/jetpack-boost/v1", "/wp-json/jetpack-global-styles/v1",
        "/wp-json/newspack-blocks/v1", "/wp-json/videopress/v1",
        "/wp-json/help-center", "/wp-json/wp-block-editor/v1",
        "/wp-json/wp-sync/v1",
    }
    for ns_root in sorted(known_ns_roots):
        fetch(f"{BASE_URL}{ns_root}", subdir="api")
        time.sleep(0.15)

    # 3) wp/v2 list endpoints
    log("  --- wp/v2 Collection Endpoints ---")
    collection_endpoints = [
        "/wp-json/wp/v2/posts", "/wp-json/wp/v2/pages",
        "/wp-json/wp/v2/media", "/wp-json/wp/v2/categories",
        "/wp-json/wp/v2/tags", "/wp-json/wp/v2/types",
        "/wp-json/wp/v2/statuses", "/wp-json/wp/v2/taxonomies",
        "/wp-json/wp/v2/users", "/wp-json/wp/v2/comments",
        "/wp-json/wp/v2/blocks", "/wp-json/wp/v2/navigation",
        "/wp-json/wp/v2/search", "/wp-json/wp/v2/statuses",
    ]
    for ep in collection_endpoints:
        fetch(f"{BASE_URL}{ep}", subdir="api")
        time.sleep(0.15)
        # Also try with per_page=100
        fetch(f"{BASE_URL}{ep}?per_page=100", subdir="api")
        time.sleep(0.1)

    # Auth-gated endpoints (save 401/403 responses for reference)
    log("  --- Auth-gated Endpoints ---")
    for ep in ["/wp-json/wp/v2/settings", "/wp-json/wp/v2/themes",
               "/wp-json/wp/v2/plugins", "/wp-json/wp/v2/block-types",
               "/wp-json/wp/v2/templates", "/wp-json/wp/v2/template-parts",
               "/wp-json/wp/v2/global-styles", "/wp-json/wp/v2/menu-items",
               "/wp-json/wp/v2/menus", "/wp-json/wp/v2/sidebars",
               "/wp-json/wp/v2/widgets", "/wp-json/wp/v2/block-directory/search"]:
        fetch(f"{BASE_URL}{ep}", subdir="api")
        time.sleep(0.1)

    # 4) Dynamically discover individual post/page/media IDs
    log("  --- Individual Items (dynamic) ---")

    def fetch_all_individual(list_endpoint, item_endpoint_template):
        """Fetch a list, extract IDs, fetch each individual item."""
        items = discover_list(list_endpoint)
        ids = []
        urls = []
        if isinstance(items, list):
            for item in items:
                if isinstance(item, dict):
                    iid = item.get("id")
                    if iid and iid not in ids:
                        ids.append(iid)
                        urls.append(item.get("link", ""))
        for iid in sorted(ids):
            ep = item_endpoint_template.format(id=iid)
            fetch(f"{BASE_URL}{ep}", subdir="api")
            time.sleep(0.1)
        return ids, urls

    post_ids, post_links = fetch_all_individual(
        "/wp-json/wp/v2/posts", "/wp-json/wp/v2/posts/{id}")
    page_ids, page_links = fetch_all_individual(
        "/wp-json/wp/v2/pages", "/wp-json/wp/v2/pages/{id}")
    media_ids, _media_links = fetch_all_individual(
        "/wp-json/wp/v2/media", "/wp-json/wp/v2/media/{id}")

    log(f"    Posts: {len(post_ids)}  Pages: {len(page_ids)}  Media: {len(media_ids)}")

    unpublished_posts, unpublished_pages = probe_unpublished_ids(post_ids, page_ids, media_ids)

    # 5) Jetpack sub-endpoints
    log("  --- Jetpack Sub-endpoints ---")
    jetpack_subs = [
        "/wp-json/jetpack/v4/site", "/wp-json/jetpack/v4/module",
        "/wp-json/jetpack/v4/module/all", "/wp-json/jetpack/v4/module/protect",
        "/wp-json/jetpack/v4/module/related-posts", "/wp-json/jetpack/v4/module/monitor",
        "/wp-json/jetpack/v4/scan", "/wp-json/jetpack/v4/scan/history",
        "/wp-json/jetpack/v4/sync/status", "/wp-json/jetpack/v4/sync/checksum",
        "/wp-json/jetpack/v4/connection", "/wp-json/jetpack/v4/connection/url",
        "/wp-json/jetpack/v4/identity-crisis", "/wp-json/jetpack/v4/plugins",
        "/wp-json/jetpack/v4/update-plugins", "/wp-json/jetpack/v4/recommendations/data",
        "/wp-json/jetpack/v4/recommendations/site-pages", "/wp-json/jetpack/v4/notice",
        "/wp-json/jetpack/v4/notice/block", "/wp-json/jetpack/v4/checkout",
        "/wp-json/jetpack/v4/backup", "/wp-json/jetpack/v4/backup-ux",
        "/wp-json/jetpack/v4/backup-ux/data", "/wp-json/jetpack/v4/stats-app",
        "/wp-json/jetpack/v4/import", "/wp-json/jetpack/v4/explat",
        "/wp-json/jetpack/v4/blaze-app", "/wp-json/jetpack/v4/blaze",
        "/wp-json/jetpack/v4/videopress", "/wp-json/jetpack/v4/social",
        "/wp-json/jetpack/v4/search", "/wp-json/jetpack/v4/search/plan",
        "/wp-json/jetpack/v4/search/settings", "/wp-json/jetpack/v4/search/stats",
        "/wp-json/jetpack/v4/verify-tracking", "/wp-json/jetpack/v4/verify-google-ads",
    ]
    for ep in jetpack_subs:
        fetch(f"{BASE_URL}{ep}", subdir="api")
        time.sleep(0.12)

    # 6) WP.com sub-endpoints
    log("  --- WP.com Sub-endpoints ---")
    for ep in ["/wp-json/wpcom/v2/sites", "/wp-json/wpcom/v2/site-verticals",
               "/wp-json/wpcom/v2/block-likes"]:
        fetch(f"{BASE_URL}{ep}", subdir="api")
        time.sleep(0.12)

    # 7) oEmbed for ALL discovered pages
    log("  --- oEmbed Endpoints ---")
    oembed_urls = set()
    # From sitemap
    oembed_urls.add(BASE_URL)
    sitemap_data = discover_sitemap_urls()
    for surl in sitemap_data:
        if surl.startswith(BASE_URL):
            oembed_urls.add(surl)
    # From discovered post/page links
    for link in post_links + page_links:
        oembed_urls.add(link)

    for ou in sorted(oembed_urls):
        encoded = urllib.parse.quote(ou, safe="")
        for fmt in ["", "&format=xml"]:
            fetch(f"{BASE_URL}/wp-json/oembed/1.0/embed?url={encoded}{fmt}", subdir="api")
            time.sleep(0.08)

    # 8) rest_route fallback
    fetch(f"{BASE_URL}/?rest_route=/", subdir="api")
    fetch(f"{BASE_URL}/?rest_route=/wp/v2", subdir="api")

    return post_ids, page_ids, media_ids, post_links, page_links, unpublished_posts, unpublished_pages


def probe_unpublished_ids(post_ids, page_ids, media_ids):
    """Broadly probe for unpublished/restricted post and page IDs (401/403)."""
    all_known = set(post_ids + page_ids + media_ids)
    if not all_known:
        return [], []

    max_id = max(all_known)
    scan_max = max_id + 300

    published_posts = set(post_ids)
    published_pages = set(page_ids)

    probe_ids = sorted(
        pid for pid in range(1, scan_max + 1)
        if pid not in published_posts and pid not in published_pages
    )

    log(f"  Probing {len(probe_ids)} IDs (1-{scan_max}) for unpublished/restricted content...")

    unpublished_posts = []
    unpublished_pages = []
    _up_lock = threading.Lock()

    def check_one(pid):
        result = _probe_fetch(f"{BASE_URL}/wp-json/wp/v2/posts/{pid}", subdir="api")
        if result[0] == "ok":
            return
        if result[2] in (401, 403):
            with _up_lock:
                unpublished_posts.append((pid, result[2]))
            return
        result2 = _probe_fetch(f"{BASE_URL}/wp-json/wp/v2/pages/{pid}", subdir="api")
        if result2[2] in (401, 403):
            with _up_lock:
                unpublished_pages.append((pid, result2[2]))

    done_count = 0
    _progress_lock = threading.Lock()

    def on_done(_):
        nonlocal done_count
        with _progress_lock:
            done_count += 1
            if done_count % 200 == 0 or done_count == len(probe_ids):
                log(f"    Progress: {done_count}/{len(probe_ids)} ...")

    with ThreadPoolExecutor(max_workers=5) as ex:
        futs = {ex.submit(check_one, pid): pid for pid in probe_ids}
        for f in as_completed(futs):
            on_done(f)

    log(f"    Found {len(unpublished_posts)} unpublished posts, {len(unpublished_pages)} unpublished pages")
    return unpublished_posts, unpublished_pages


def generate_unpublished_report(unpublished_posts, unpublished_pages):
    """Write a report of unpublished/restricted IDs to UNPUBLISHED_IDS.md."""
    if not unpublished_posts and not unpublished_pages:
        log("  No unpublished items found, skipping report")
        return

    total = len(unpublished_posts) + len(unpublished_pages)
    lines = [
        "# Unpublished / Restricted IDs Report",
        "",
        f"**Generated:** {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}",
        f"**Source:** {BASE_URL}/wp-json/wp/v2/{{posts,pages}}/{{id}}",
        "",
        "IDs returning 401 (Unauthorized) or 403 (Forbidden) indicate content",
        "that exists but is not publicly accessible (drafts, private, etc.).",
        "",
        "## Summary",
        "",
        "| Type | Count |",
        "|------|-------|",
        f"| **Posts** | {len(unpublished_posts)} |",
        f"| **Pages** | {len(unpublished_pages)} |",
        f"| **Total** | {total} |",
        "",
    ]

    for kind_name, entries in [("Posts", unpublished_posts), ("Pages", unpublished_pages)]:
        if entries:
            lines += ["", f"## Unpublished {kind_name}", "", "| ID | Status |", "|----|--------|"]
            for iid, status in sorted(entries):
                lines.append(f"| {iid} | {status} |")
            lines.append("")

    lines.append("")
    lines.append("*Auto-generated by update_mirror.py*")
    lines.append("")

    out_path = MIRROR_DIR / "UNPUBLISHED_IDS.md"
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    log(f"  Unpublished IDs report: {out_path}")


# --- Phase 4: Media ---

def fetch_media(post_links, page_links, sitemap_urls):
    log("=== FETCHING MEDIA FILES ===")

    media_urls = set()

    # 1) From image sitemap
    for url, typ in sitemap_urls.items():
        if typ == "image":
            media_urls.add(url)

    # 2) From saved media JSON (source_url field)
    api_dir = MIRROR_DIR / "api"
    if api_dir.exists():
        for jf in api_dir.rglob("*.json"):
            try:
                data = json.loads(jf.read_bytes())
            except (json.JSONDecodeError, ValueError):
                continue
            items = data if isinstance(data, list) else [data]
            for item in items:
                if isinstance(item, dict):
                    src = item.get("source_url") or item.get("guid", {}).get("rendered", "")
                    if src and src.startswith("http"):
                        media_urls.add(src)

    # 3) Parse HTML for wp-content/uploads/ URLs
    html_resources = extract_html_resource_urls("html")
    for u in html_resources:
        if "/wp-content/uploads/" in u and u.startswith((BASE_URL, "https://i0.wp.com")):
            # Strip query params for the base image URL
            clean = u.split("?")[0]
            media_urls.add(clean)

    # 4) Parse API JSON responses for image URLs
    for link_set in [post_links, page_links]:
        for link in link_set:
            if link:
                # Try to find featured_media and its URL from already-fetched post JSON
                pass  # handled by #2 above

    log(f"  Discovered {len(media_urls)} media URLs")
    for url in sorted(media_urls):
        if url.startswith((BASE_URL, "https://i0.wp.com")):
            fetch(url, subdir="media", binary=True)
            time.sleep(0.15)

    # Also fetch known thumbnail variations from media API
    # (they may not be directly listed, but source_url exists)
    api_dir = MIRROR_DIR / "api"
    if api_dir.exists():
        for jf in sorted(api_dir.rglob("wp-json/wp/v2/media/*.json")):
            try:
                data = json.loads(jf.read_bytes())
                if isinstance(data, dict):
                    src = data.get("source_url", "")
                    if src and "wp-content/uploads" in src:
                        base = src.rsplit(".", 1)[0]
                        ext = src.rsplit(".", 1)[-1]
                        # Try -150x150, -300x300, etc.
                        for suffix in ["-150x150", "-300x300", "-768x768", "-1024x1024"]:
                            thumb = f"{base}{suffix}.{ext}"
                            fetch(thumb, subdir="media", binary=True)
                            time.sleep(0.08)
            except (json.JSONDecodeError, ValueError):
                pass


# --- Phase 5: Theme Assets ---

def fetch_theme_assets():
    log("=== FETCHING THEME ASSETS ===")
    theme_base = f"{BASE_URL}/wp-content/themes/perenne"

    # Probe common theme paths
    probe_paths = [
        "/style.css", "/theme.json", "/readme.txt",
        "/screenshot.png", "/index.php", "/functions.php",
        "/header.php", "/footer.php", "/single.php",
        "/page.php", "/archive.php", "/404.php",
        "/search.php", "/sidebar.php", "/comments.php",
        "/front-page.php",
        "/assets/css/main.css", "/assets/css/blocks.css",
        "/assets/js/navigation.js", "/assets/js/script.js",
        "/assets/fonts/ibm-plex-mono_normal_400.ttf",
        "/assets/fonts/ibm-plex-mono_italic_400.ttf",
    ]

    for tp in probe_paths:
        url = f"{theme_base}{tp}"
        binary = tp.endswith((".ttf", ".woff2", ".png", ".jpg", ".ico"))
        fetch(url, subdir="assets", binary=binary)
        time.sleep(0.12)

    # Also discover theme assets from HTML
    html_resources = extract_html_resource_urls("html")
    for u in html_resources:
        if "/wp-content/themes/" in u:
            fetch(u, subdir="assets", binary=u.endswith((".ttf", ".woff2", ".png", ".jpg", ".ico")))
            time.sleep(0.1)


# --- Phase 6: Plugin Assets ---

def fetch_plugin_assets():
    log("=== FETCHING PLUGIN ASSETS ===")
    # Probe common plugin paths
    plugin_probes = [
        "jetpack/modules/related-posts/related-posts.css",
        "jetpack/modules/likes/style.css",
        "jetpack/_inc/build/likes/style.min.css",
        "jetpack/modules/carousel/jetpack-carousel.css",
        "jetpack/_inc/build/carousel/jetpack-carousel.min.js",
        "jetpack/_inc/blocks/swiper.js",
        "jetpack/modules/stats/gravatar-hovercards.css",
        "jetpack/modules/theme-tools/compat/perenne.css",
        "gutenberg/build/scripts/dom-ready/index.min.js",
        "gutenberg/build/styles/block-library/paragraph/style.min.css",
        "gutenberg/build/styles/block-library/group/style.min.css",
        "gutenberg/build/styles/block-library/site-logo/style.min.css",
        "gutenberg/build/styles/block-library/post-date/style.min.css",
        "gutenberg/build/styles/block-library/post-title/style.min.css",
        "gutenberg/build/styles/block-library/spacer/style.min.css",
        "gutenberg/build/styles/block-library/post-content/style.min.css",
        "gutenberg/build/styles/block-library/post-navigation-link/style.min.css",
        "gutenberg/build/styles/block-library/heading/style.min.css",
        "gutenberg/build/styles/block-library/post-featured-image/style.min.css",
        "gutenberg/build/styles/block-library/quote/style.min.css",
        "gutenberg/build/styles/block-library/image/style.min.css",
        "gutenberg/build/styles/block-library/post-terms/style.min.css",
        "gravatar-enhanced/build/patterns-view.css",
        "wp-statistics/assets/js/tracker.js",
    ]
    for pp in plugin_probes:
        url = f"{BASE_URL}/wp-content/plugins/{pp}"
        fetch(url, subdir="assets")
        time.sleep(0.1)

    # Also discover plugin assets from HTML (catches any we missed)
    html_resources = extract_html_resource_urls("html")
    for u in html_resources:
        if "/wp-content/plugins/" in u:
            fetch(u, subdir="assets")
            time.sleep(0.1)


# --- Phase 7: Discovery ---

def fetch_discovery():
    log("=== FETCHING DISCOVERY DOCUMENTS ===")
    for path in ["/robots.txt", "/sitemap.xml", "/sitemap-1.xml",
                 "/image-sitemap-1.xml", "/news-sitemap.xml",
                 "/sitemap.xsl", "/sitemap-index.xsl",
                 "/image-sitemap.xsl", "/news-sitemap.xsl"]:
        fetch(f"{BASE_URL}{path}", subdir="discovery")
        time.sleep(0.1)


# --- Phase 8: Extras ---

def fetch_extras():
    log("=== FETCHING EXTRAS ===")
    extras = [
        (f"{BASE_URL}/readme.html", "extras", False),
        (f"{BASE_URL}/license.txt", "extras", False),
        (f"{BASE_URL}/wp-config-sample.php", "extras", False),
        (f"{BASE_URL}/favicon.ico", "extras", True),
        (f"{BASE_URL}/xmlrpc.php?rsd", "extras", False),
        (f"{BASE_URL}/xmlrpc.php", "extras", False),
        (f"{BASE_URL}/wp-admin/css/install.css", "extras", False),
        (f"{BASE_URL}/wp-admin/images/wordpress-logo.png", "extras", True),
    ]
    for url, subdir, binary in extras:
        fetch(url, subdir=subdir, binary=binary, save_headers=not binary)
        time.sleep(0.12)


# --- Phase 9: Third-party CDN ---

def fetch_third_party():
    log("=== FETCHING THIRD-PARTY CDN ASSETS ===")

    # Stats/analytics scripts
    for url in [
        "https://s0.wp.com/wp-content/js/bilmur.min.js?m=202622",
        "https://stats.wp.com/e-202622.js",
    ]:
        fetch(url, subdir="third_party")
        time.sleep(0.2)

    # Discover font references from HTML and theme CSS
    html_resources = extract_html_resource_urls("html")
    # Also scan theme CSS
    for css_dir in ["assets", "html"]:
        d = MIRROR_DIR / css_dir
        if d.exists():
            for cf in d.rglob("*.css"):
                text = cf.read_text(encoding="utf-8", errors="replace")
                for m in re.finditer(r'''url\(['"]?([^'")\s]+)['"]?\)''', text):
                    u = m.group(1)
                    if u.startswith("//"):
                        u = "https:" + u
                    if u.startswith("http"):
                        html_resources.add(u)

    font_urls = {u for u in html_resources if "fonts." in u or ".woff2" in u or ".woff" in u}
    if not font_urls:
        # Known IBM Plex Mono fonts (probe if not discovered)
        for u in [
            "https://fonts.wp.com/s/ibmplexmono/v19/-F63fjptAgt5VM-kVkqdyU8n5i0g1l9kn-s.woff2",
            "https://fonts.wp.com/s/ibmplexmono/v19/-F6pfjptAgt5VM-kVkqdyU8n1ioq131hj-sNFQ.woff2",
            "https://fonts.wp.com/s/ibmplexmono/v19/-F6pfjptAgt5VM-kVkqdyU8n3kwq131hj-sNFQ.woff2",
            "https://fonts.wp.com/s/ibmplexmono/v19/-F6qfjptAgt5VM-kVkqdyU8n3oQI8lJPg-IUDNg.woff2",
            "https://fonts.wp.com/s/ibmplexmono/v19/-F6qfjptAgt5VM-kVkqdyU8n3pQP8lJPg-IUDNg.woff2",
            "https://fonts.wp.com/s/ibmplexmono/v19/-F6qfjptAgt5VM-kVkqdyU8n3twJ8lJPg-IUDNg.woff2",
            "https://fonts.wp.com/s/ibmplexmono/v19/-F6qfjptAgt5VM-kVkqdyU8n3uAL8lJPg-IUDNg.woff2",
            "https://fonts.wp.com/s/ibmplexmono/v19/-F6qfjptAgt5VM-kVkqdyU8n3vAO8lJPg-IUDNg.woff2",
            "https://fonts.wp.com/s/ibmplexmono/v19/-F6rfjptAgt5VM-kVkqdyU8n1ioStndgre4dFcFh.woff2",
            "https://fonts.wp.com/s/ibmplexmono/v19/-F6sfjptAgt5VM-kVkqdyU8n1ioSClNFgsARHNh4zg.woff2",
            "https://fonts.wp.com/s/ibmplexmono/v19/-F6sfjptAgt5VM-kVkqdyU8n1ioSGlZFgsARHNh4zg.woff2",
            "https://fonts.wp.com/s/ibmplexmono/v19/-F6sfjptAgt5VM-kVkqdyU8n1ioSJlRFgsARHNh4zg.woff2",
            "https://fonts.wp.com/s/ibmplexmono/v19/-F6sfjptAgt5VM-kVkqdyU8n1ioSblJFgsARHNh4zg.woff2",
            "https://fonts.wp.com/s/ibmplexmono/v19/-F6sfjptAgt5VM-kVkqdyU8n1ioSflVFgsARHNh4zg.woff2",
        ]:
            font_urls.add(u)

    for url in sorted(font_urls):
        fetch(url, subdir="third_party", binary=True)
        time.sleep(0.12)


# --- Phase 10: External References ---

def fetch_external_references():
    log("=== FETCHING EXTERNAL REFERENCES ===")
    refs = [
        "https://www.reddit.com/r/NoMansSkyTheGame/comments/1tczflq/connection_detected_access_denied.json",
        "https://www.reddit.com/r/NoMansSkyTheGame/comments/1tczflq/connection_detected_access_denied/",
        "https://forums.atlas-65.com/t/project-skyscraper-no-mans-sky-arg/9095/180.json",
        "https://forums.atlas-65.com/t/project-skyscraper-no-mans-sky-arg/9095/180",
    ]
    for url in refs:
        try:
            r = urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": USER_AGENT}), timeout=15)
            content = r.read()
            path = url_to_path(url, subdir="endpoints")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
            stats["fetched"] += 1
            log(f"  OK   {url}")
        except Exception as e:
            stats["failed"] += 1
            log(f"  FAIL {url} -> {e}")
        time.sleep(0.5)


# --- Phase 11: Additional Endpoint Probes ---

def fetch_additional_endpoints():
    log("=== FETCHING ADDITIONAL ENDPOINTS ===")
    for path in [
        "/wp-content/debug.log", "/wp-content/uploads/",
        "/wp-includes/", "/wp-admin/",
        "/.htaccess", "/.git/config", "/.env",
    ]:
        fetch(f"{BASE_URL}{path}", subdir="endpoints")
        time.sleep(0.12)


# --- Phase 12: Manifest & Diff ---

def generate_manifest():
    log("=== GENERATING MANIFEST ===")
    total_size = 0
    total_files = 0
    section_data = {}

    for root, dirs, files in os.walk(str(MIRROR_DIR)):
        dirs[:] = [d for d in dirs if d not in (".git", "__pycache__")]
        for fn in files:
            fp = os.path.join(root, fn)
            rel = os.path.relpath(fp, str(MIRROR_DIR))
            sz = os.path.getsize(fp)
            total_files += 1
            total_size += sz
            parts = rel.replace("\\", "/").split("/")
            section = parts[0] if parts else "root"
            section_data.setdefault(section, {"files": 0, "size": 0})
            section_data[section]["files"] += 1
            section_data[section]["size"] += sz

    lines = [
        f"# project-skyscraper.com \u2014 Complete Mirror Manifest",
        "",
        f"**Generated:** {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}",
        f"**Source:** {BASE_URL}",
        "",
        "## Summary",
        "",
        "| Metric | Value |",
        "|--------|-------|",
        f"| **Total files** | {total_files} |",
        f"| **Total size** | {_fmt_size(total_size)} |",
        "",
        "## Section Breakdown",
        "",
        "| Section | Files | Size |",
        "|---------|-------|------|",
    ]
    for section in sorted(section_data):
        if section in ("update_mirror.py", "MIRROR_MANIFEST.md"):
            continue
        d = section_data[section]
        lines.append(f"| **{section}/** | {d['files']} | {_fmt_size(d['size'])} |")
    lines += ["", "## HTML Pages", ""]
    hdir = MIRROR_DIR / "html"
    if hdir.exists():
        for hf in sorted(hdir.glob("*.html")):
            lines.append(f"- `{hf.stem}` ({_fmt_size(hf.stat().st_size)})")
    lines += ["", "## Media Files", ""]
    mdir = MIRROR_DIR / "media"
    if mdir.exists():
        for mf in sorted(mdir.rglob("*")):
            if mf.is_file():
                rel = str(mf.relative_to(MIRROR_DIR)).replace("\\", "/")
                lines.append(f"- `{rel}` ({_fmt_size(mf.stat().st_size)})")
    lines += ["", "## API Endpoints", ""]
    adir = MIRROR_DIR / "api"
    if adir.exists():
        for af in sorted(adir.rglob("*")):
            if af.is_file():
                rel = str(af.relative_to(MIRROR_DIR)).replace("\\", "/")
                lines.append(f"- `{rel}` ({_fmt_size(af.stat().st_size)})")
    lines += ["", "*Manifest auto-generated by update_mirror.py*", ""]

    manifest_path = MIRROR_DIR / "MIRROR_MANIFEST.md"
    manifest_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    log(f"  Manifest written: {manifest_path} ({total_files} files, {_fmt_size(total_size)})")


def _diff_has_real_changes(diff_text):
    """Check if a unified diff contains changes beyond WordPress auto-generated noise."""
    changed = []
    for line in diff_text.splitlines():
        if line.startswith(('--- ', '+++ ', '@@', '#', 'diff --git')):
            continue
        if line.startswith(('-', '+')):
            changed.append(line)
    if not changed:
        return False
    for line in changed:
        if _is_noise_line(line):
            continue
        if line.startswith('-') and '+' in line:
            parts = line[1:].split('+', 1)
            if len(parts) == 2 and parts[0].rstrip() == parts[1].rstrip():
                continue
        prefix = line[0]
        other = '-' if prefix == '+' else '+'
        stripped = line[1:].rstrip()
        paired = any(
            l[0] == other and l[1:].rstrip() == stripped
            for l in changed
        )
        if not paired:
            return True
    return False


def _reverse_patch(new_text: str, diff_content: str) -> str:
    """Apply unified diff hunks in reverse to reconstruct the original (old) content."""
    lines = new_text.splitlines(keepends=True)
    hunks = []
    diff_lines = diff_content.splitlines(keepends=True)
    for i, line in enumerate(diff_lines):
        ls = line.rstrip('\n\r')
        m = re.match(r'^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@', ls)
        if not m:
            continue
        new_start = int(m.group(3)) - 1
        new_count = int(m.group(4)) if m.group(4) else 1
        old_hunk = []
        j = i + 1
        while j < len(diff_lines):
            dl = diff_lines[j].rstrip('\n\r')
            if re.match(r'^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@', dl):
                break
            if dl and dl[0] in ('-', ' '):
                old_hunk.append(dl[1:] + '\n')
            j += 1
        hunks.append((new_start, new_count, old_hunk))
    for new_start, new_count, old_hunk in reversed(hunks):
        lines[new_start:new_start + new_count] = old_hunk
    return "".join(lines)


def _forward_patch(old_text: str, diff_content: str) -> str:
    """Apply unified diff to reconstruct the new content from old."""
    lines = old_text.splitlines(keepends=True)
    hunks = []
    diff_lines = diff_content.splitlines(keepends=True)
    for i, line in enumerate(diff_lines):
        ls = line.rstrip('\n\r')
        m = re.match(r'^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@', ls)
        if not m:
            continue
        old_start = int(m.group(1)) - 1
        old_count = int(m.group(2)) if m.group(2) else 1
        new_hunk = []
        j = i + 1
        while j < len(diff_lines):
            dl = diff_lines[j].rstrip('\n\r')
            if re.match(r'^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@', dl):
                break
            if dl and dl[0] in ('+', ' '):
                new_hunk.append(dl[1:] + '\n')
            j += 1
        hunks.append((old_start, old_count, new_hunk))
    for old_start, old_count, new_hunk in reversed(hunks):
        lines[old_start:old_start + old_count] = new_hunk
    return "".join(lines)


def _rebuild_diff(url: str, path: Path, old_raw: bytes, new_raw: bytes) -> str:
    """Generate a beautified diff between old and new raw content."""
    rel = str(path.relative_to(MIRROR_DIR)).replace("\\", "/")
    old_text = old_raw.decode("utf-8", errors="replace")
    new_text = new_raw.decode("utf-8", errors="replace")
    old_text = _beautify_content(old_text, path)
    new_text = _beautify_content(new_text, path)
    old_lines = old_text.splitlines(keepends=True)
    new_lines = new_text.splitlines(keepends=True)
    diff_lines = list(difflib.unified_diff(
        old_lines, new_lines,
        fromfile=f"old/{rel}", tofile=f"new/{rel}", n=3
    ))
    header = [
        f"# Diff: {url}", f"# File: {rel}",
        f"# Timestamp: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}",
        f"# Lines: {len(old_lines)} old -> {len(new_lines)} new",
        "# Beautified: yes", "",
    ]
    return "\n".join(header) + "".join(diff_lines)


def _migrate_diffs():
    """Rebuild all existing .diff files with beautification (one-time migration)."""
    diff_dir = MIRROR_DIR / "diffs"
    if not diff_dir.exists():
        return
    migrated = 0
    errors = 0
    for diff_file in sorted(diff_dir.glob("*.diff")):
        content = diff_file.read_text(encoding="utf-8")
        if "# Beautified: yes" in content:
            continue
        if "# Binary:" in content:
            continue
        try:
            url_m = re.search(r'^# Diff: (.+)', content, re.MULTILINE)
            rel_m = re.search(r'^# File: (.+)', content, re.MULTILINE)
            if not url_m or not rel_m:
                continue
            url = url_m.group(1)
            rel = rel_m.group(1)
            content_path = MIRROR_DIR / rel
            if not content_path.is_file():
                continue
            new_raw = content_path.read_bytes()
            if len(new_raw) < 20:
                continue
            new_text = new_raw.decode("utf-8", errors="replace")
            old_text = _reverse_patch(new_text, content)
            if old_text == new_text or len(old_text) < 20:
                # Reverse patch failed; try forward patch (current file as old)
                old_text = new_text
                new_text = _forward_patch(old_text, content)
                if new_text == old_text or len(new_text) < 20:
                    continue
                old_raw = old_text.encode("utf-8")
                new_raw = new_text.encode("utf-8")
            else:
                old_raw = old_text.encode("utf-8")
            new_diff = _rebuild_diff(url, content_path, old_raw, new_raw)
            if not _diff_has_real_changes(new_diff):
                diff_file.unlink()
                log(f"  MIGRATED (removed): {diff_file.name}")
                migrated += 1
                continue
            diff_file.write_text(new_diff, encoding="utf-8")
            migrated += 1
            log(f"  MIGRATED: {diff_file.name} ({len(new_diff)} bytes)")
        except Exception as e:
            log(f"  MIGRATE ERR {diff_file.name}: {e}")
            errors += 1
    if migrated or errors:
        log(f"  Diff migration complete: {migrated} rebuilt, {errors} errors")


def _parse_changelog(path: Path) -> dict:
    """Parse existing CHANGELOG.md into a dict of {url: {rel, size, history}}.

    Each history entry is a dict with keys: timestamp, is_binary, lines.
    Lines are raw diff content without ``` markers or HTML wrappers.
    """
    if not path.is_file():
        return {}
    text = path.read_text(encoding="utf-8")
    entries = {}
    current = None
    history_lines = None
    in_details = False
    in_details_diff = False
    details_lines = None

    for line in text.splitlines():
        if line.startswith("### "):
            if current is not None:
                if history_lines is not None:
                    _finalize_history(current, history_lines)
                entries[current["url"]] = current
            url = line[4:].strip()
            current = {"url": url, "rel": "", "size": "", "history": []}
            history_lines = None
            in_details = False
            in_details_diff = False
            details_lines = None
        elif current is not None:
            if current["rel"] == "" and re.match(r'^`[^`]+`\s*\(', line):
                m = re.match(r'^`([^`]+)`\s*\(([^)]+)\)', line)
                if m:
                    current["rel"] = m.group(1)
                    current["size"] = m.group(2)
            elif line.startswith("<details>"):
                in_details = True
                in_details_diff = False
                details_lines = []
            elif line.startswith("</details>"):
                if details_lines is not None and current is not None:
                    ts = _extract_details_timestamp(details_lines)
                    current["history"].append({
                        "timestamp": ts,
                        "lines": details_lines[:],
                    })
                in_details = False
                in_details_diff = False
                details_lines = None
            elif in_details:
                if line.startswith("```diff"):
                    in_details_diff = True
                elif line.startswith("```") and in_details_diff:
                    in_details_diff = False
                elif in_details_diff:
                    if not line.startswith("<"):
                        details_lines.append(line)
            elif line.startswith("```diff"):
                history_lines = []
            elif line.startswith("```"):
                if history_lines is not None:
                    _finalize_history(current, history_lines)
                    history_lines = None
            else:
                if history_lines is not None:
                    history_lines.append(line)

    if current is not None:
        if history_lines is not None:
            _finalize_history(current, history_lines)
        entries[current["url"]] = current

    return entries


def _finalize_history(current: dict, lines: list):
    """Store current diff lines into history (or set as current primary)."""
    entry = {"timestamp": "", "is_binary": False, "lines": lines[:]}
    for l in lines:
        m = re.match(r"^# Timestamp: (.+)", l)
        if m:
            entry["timestamp"] = m.group(1)
            break
    current.setdefault("history", []).append(entry)


def _extract_details_timestamp(lines: list) -> str:
    """Extract timestamp from a <details> block's first diff header."""
    for l in lines:
        m = re.match(r"^# Timestamp: (.+)", l)
        if m:
            return m.group(1)
    return ""


def store_diff():
    if not changes:
        return
    diff_dir = MIRROR_DIR / "diffs"
    changelog_path = diff_dir / "CHANGELOG.md"
    meaningful = 0

    old_entries = _parse_changelog(changelog_path)

    new_entries = {}
    for url, path in changes:
        rel = str(path.relative_to(MIRROR_DIR)).replace("\\", "/")
        sz = _fmt_size(path.stat().st_size)

        safe_name = re.sub(r'[^a-zA-Z0-9_\-.]', '_', rel) + ".diff"
        diff_file = diff_dir / safe_name
        is_real = False
        if diff_file.is_file():
            content = diff_file.read_text(encoding="utf-8")
            is_real = _diff_has_real_changes(content)
        else:
            is_real = True

        if not is_real:
            continue

        meaningful += 1

        old_entry = old_entries.get(url)
        history = []
        if old_entry:
            for h in old_entry.get("history", []):
                history.append(h)

        if diff_file.is_file():
            content = diff_file.read_text(encoding="utf-8")
            current_lines = _filter_noise_diff_lines(content.splitlines())
        else:
            current_lines = None

        new_entries[url] = {
            "rel": rel,
            "size": sz,
            "current_diff": current_lines,
            "history": history,
        }

    if meaningful == 0:
        return

    lines = [
        "# Change Report",
        "",
        f"Last updated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}",
        "",
        f"**{meaningful} file(s) with meaningful changes**",
        "",
    ]

    for url in sorted(new_entries.keys()):
        entry = new_entries[url]
        lines += [f"### {url}", f"`{entry['rel']}` ({entry['size']})", ""]

        if entry["current_diff"] is not None:
            lines.append("```diff")
            lines += entry["current_diff"]
            lines.append("```")
            lines.append("")
        else:
            lines.append("*No line-level diff available (binary or probe fetch)*")
            lines.append("")

        for h in entry["history"]:
            ts = h["timestamp"]
            label = f"Previous diff ({ts})" if ts else "Previous diff"
            hist_lines = _filter_noise_diff_lines(h["lines"])
            lines.append("<details>")
            lines.append(f"<summary>{label}</summary>")
            lines.append("")
            lines.append("```diff")
            if hist_lines and hist_lines[-1] == "```":
                lines += hist_lines[:-1]
            else:
                lines += hist_lines
            lines.append("```")
            lines.append("")
            lines.append("</details>")
            lines.append("")

    lines.append("*Auto-generated by update_mirror.py*")
    lines.append("")

    changelog_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    log(f"  Change report saved: {changelog_path} ({meaningful} files)")


def _parse_old_report(text: str) -> dict:
    """Parse a dated change_*.md report into {url: {lines, timestamp}}."""
    entries = {}
    current = None
    current_lines = None
    header_match = re.search(r'([\d-]+ [\d:]+ UTC)', text.splitlines()[0] if text.splitlines() else "")
    report_ts = header_match.group(1) if header_match else ""

    for line in text.splitlines():
        if line.startswith("### "):
            if current is not None and current_lines is not None:
                entries[current] = {"timestamp": report_ts, "lines": current_lines}
            current = line[4:].strip()
            current_lines = None
        elif current is not None:
            if current_lines is None and line.startswith("```diff"):
                current_lines = []
            elif current_lines is not None:
                current_lines.append(line)
                if line.startswith("```") and not line.startswith("```diff"):
                    entries[current] = {"timestamp": report_ts, "lines": current_lines}
                    current = None
                    current_lines = None

    if current is not None and current_lines is not None:
        entries[current] = {"timestamp": report_ts, "lines": current_lines}

    return entries


def _migrate_old_reports(diff_dir: Path):
    """Migrate old change_*.md reports into CHANGELOG.md if it doesn't exist."""
    changelog_path = diff_dir / "CHANGELOG.md"
    if changelog_path.is_file():
        return

    old_reports = sorted(diff_dir.glob("change_*.md"))
    if not old_reports:
        return

    per_file = {}
    for report_path in old_reports:
        text = report_path.read_text(encoding="utf-8")
        entries = _parse_old_report(text)
        for url, data in entries.items():
            per_file.setdefault(url, []).append(data)

    lines = [
        "# Change Report",
        "",
        "*Migrated from historical change reports*",
        "",
    ]

    for url in sorted(per_file.keys()):
        history = per_file[url]
        recent = history[-1]
        older = history[:-1]

        path = url_to_path(url)
        mig_rel = str(path.relative_to(MIRROR_DIR)).replace("\\", "/")
        lines += [f"### {url}", f"`{mig_rel}`", "", "```diff"]
        if recent["lines"] and recent["lines"][-1] == "```":
            lines += recent["lines"][:-1]
        else:
            lines += recent["lines"]
        lines += ["```", ""]

        for old_entry in reversed(older):
            ts = old_entry["timestamp"]
            label = f"Previous diff ({ts})" if ts else "Previous diff"
            lines.append("<details>")
            lines.append(f"<summary>{label}</summary>")
            lines.append("")
            lines.append("```diff")
            if old_entry["lines"] and old_entry["lines"][-1] == "```":
                lines += old_entry["lines"][:-1]
            else:
                lines += old_entry["lines"]
            lines.append("```")
            lines.append("")
            lines.append("</details>")
            lines.append("")

    lines.append("*Migrated from historical reports by update_mirror.py*")
    lines.append("")

    changelog_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    log(f"  MIGRATED {len(old_reports)} old report(s) into CHANGELOG.md")


# --- Unresolved Marker Files ---

NS_ROOTS_DIR = [
    "wp-json/wp/v2", "wp-json/jetpack/v4", "wp-json/wpcom/v2",
    "wp-json/wpcom/v3", "wp-json/wpcomsh/v1", "wp-json/code-snippets/v1",
    "wp-json/crowdsignal-forms/v1", "wp-json/wp-statistics/v2",
    "wp-json/wp-site-health/v1", "wp-json/wp-abilities/v1",
    "wp-json/akismet/v1", "wp-json/my-jetpack/v1",
    "wp-json/jetpack-boost/v1", "wp-json/jetpack-global-styles/v1",
    "wp-json/newspack-blocks/v1", "wp-json/videopress/v1",
    "wp-json/help-center", "wp-json/wp-block-editor/v1",
    "wp-json/wp-sync/v1", "wp-json/oembed/1.0",
]

EXAMPLES_NS_SUBS = {
    "wp-json/wp/v2": (
        "posts, pages, media, categories, tags, types, statuses, "
        "taxonomies, users, comments, blocks, navigation, search, "
        "settings, themes, plugins, block-types, templates, "
        "template-parts, global-styles, menu-items, menus, "
        "sidebars, widgets, block-directory/search"
    ),
    "wp-json/jetpack/v4": (
        "site, module, scan, sync, connection, plugins, "
        "recommendations, backup, stats-app, import, search, social, "
        "blaze, videopress"
    ),
    "wp-json/oembed/1.0": (
        "embed (requires ?url= query parameter)"
    ),
}


def generate_unresolved_markers():
    """Create .unresolved marker files for endpoints that live as
    directories and can never be fetched directly."""
    api_dir = MIRROR_DIR / "api"
    for ns in NS_ROOTS_DIR:
        unresolved = api_dir / (ns + ".unresolved")
        if unresolved.is_file():
            continue
        sub_info = EXAMPLES_NS_SUBS.get(ns, "sub-endpoints")
        unresolved.parent.mkdir(parents=True, exist_ok=True)
        lines = [
            f"Unfetchable endpoint: /{ns}",
            "",
            "This is a namespace root (directory) in the WordPress REST API.",
            "It cannot be fetched directly because multiple endpoints live",
            "under this path.",
            "",
            "Available sub-endpoints: " + sub_info,
            "",
            "Request a specific sub-endpoint instead.",
            "",
            "Generated by update_mirror.py",
        ]
        unresolved.write_text("\n".join(lines) + "\n")
        log(f"  MARKER {ns}.unresolved")


# --- ID Series Analysis ---

def generate_id_series_analysis():
    """Analyze post/page/media IDs, categorize by digit count, compute deltas."""
    api_base = MIRROR_DIR / "api" / "wp-json" / "wp" / "v2"

    def collect(endpoint_dir):
        acc = []
        d = api_base / endpoint_dir
        if d.is_dir():
            for jf in sorted(d.glob("*.json")):
                try:
                    data = json.loads(jf.read_bytes())
                except (json.JSONDecodeError, ValueError):
                    continue
                if isinstance(data, dict) and "id" in data and "date" in data:
                    acc.append((data["id"], data.get("date", ""), data.get("slug", "")))
        return acc

    all_posts = collect("posts")
    all_pages = collect("pages")
    all_media = collect("media")

    if not any([all_posts, all_pages, all_media]):
        log("  No ID data found, skipping series analysis")
        return

    def series_label(id_val):
        if id_val < 1000:
            return "A (3-digit)"
        elif id_val < 10000:
            return "B (4-digit)"
        else:
            return "C (other)"

    def build_series(items, label):
        by_series = {}
        for iid, date, slug in items:
            ser = series_label(iid)
            by_series.setdefault(ser, []).append((iid, date, slug))
        lines = [
            f"### Series {label}",
            "",
            "```",
            f"    {'ID':>6}   {'Delta':>6}   {'Date':<12}   Slug",
            "    " + "-" * 50,
        ]
        for ser_key in sorted(by_series.keys()):
            entries = sorted(by_series[ser_key])
            for idx, (iid, date, slug) in enumerate(entries):
                if idx == 0:
                    delta = "  --"
                else:
                    d = iid - entries[idx - 1][0]
                    delta = f"{d:+5d}"
                lines.append(f"    {iid:>6}   {delta:>6}   {date:<12}   {slug}")
        lines.append("```\n")
        return "\n".join(lines)

    def series_title(label):
        parts = label.split()
        tag = parts[0][0].upper()
        desc = " ".join(parts[1:]) if len(parts) > 1 else ""
        return f"### Series {tag}: {desc}" if desc else f"### Series {tag}"

    sections = []
    for label in ["A (3-digit)", "B (4-digit)", "C (other)"]:
        subset = [(iid, date, slug) for iid, date, slug in all_posts + all_pages + all_media
                  if series_label(iid) == label]
        if subset:
            subset.sort(key=lambda x: x[0])
            lines = [series_title(label), ""]
            lines.append("```")
            lines.append(f"    {'ID':>6}   {'Delta':>6}   {'Date':<12}   {'Type':<8}   Slug")
            lines.append("    " + "-" * 60)
            type_map = {}
            for iid, date, slug in all_posts:
                type_map[iid] = "post"
            for iid, date, slug in all_pages:
                type_map[iid] = "page"
            for iid, date, slug in all_media:
                type_map[iid] = "media"
            for idx, (iid, date, slug) in enumerate(subset):
                if idx == 0:
                    delta = "  --"
                else:
                    d = iid - subset[idx - 1][0]
                    delta = f"{d:+5d}"
                typ = type_map.get(iid, "?")
                lines.append(f"    {iid:>6}   {delta:>6}   {date:<12}   {typ:<8}   {slug}")
            lines.append("```\n")
            sections.append("\n".join(lines))

    combined = [
        "# Post/Page/Media ID Series Analysis",
        "",
        f"**Generated:** {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}",
        f"**Source:** {BASE_URL}/wp-json/wp/v2/{{posts,pages,media}}",
        "",
        "IDs are categorized by digit count:",
        "",
        "| Series | Digit Count | Range |",
        "|--------|-------------|-------|",
        "| **A** | 3-digit | < 1000 |",
        "| **B** | 4-digit | 1000\u20139999 |",
        "| **C** | Other | >= 10000 |",
        "",
    ]
    for s in sections:
        combined.append(s)
    combined.append("*Auto-generated by update_mirror.py*\n")

    out_path = MIRROR_DIR / "POST_ID_SERIES.md"
    out_path.write_text("\n".join(combined) + "\n", encoding="utf-8")
    log(f"  ID series written: {out_path}")


# --- Time Reference Table ---

def _parse_visible_date(html_text):
    """Extract the visible <time datetime> from post/page HTML."""
    m = re.search(r'<time\s+datetime="([^"]+)"', html_text)
    if m:
        return m.group(1)
    return None


def _parse_meta_time(html_text, prop):
    """Extract meta tag content by property, e.g. article:published_time."""
    m = re.search(r'<meta\s+property="' + re.escape(prop) + r'"\s+content="([^"]+)"', html_text)
    if m:
        return m.group(1)
    m = re.search(r'<meta\s+content="([^"]+)"\s+property="' + re.escape(prop) + r'"', html_text)
    if m:
        return m.group(1)
    return None


def _extract_epoch_timestamps(html_text):
    """Extract Unix epoch timestamps from post content (e.g. 'memory bloc 1113384720:')."""
    epochs = []
    for m in re.finditer(r'memory\s+bloc\s+(\d{9,10})', html_text, re.IGNORECASE):
        ts = int(m.group(1))
        if 1000000000 < ts < 2000000000:
            epochs.append(("memory_bloc", ts))
    # Handle both raw quotes and HTML-encoded &quot; entities
    for m in re.finditer(r'created_timestamp(?:[":\s]|&quot;)+(\d{9,10})', html_text):
        ts = int(m.group(1))
        if 1000000000 < ts < 2000000000:
            epochs.append(("exif_created", ts))
    return epochs


def _epoch_to_str(epoch_sec):
    return datetime.fromtimestamp(epoch_sec, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def _parse_url_date(link):
    """Extract the date segment from a WordPress post permalink like /2026/05/03/slug/."""
    m = re.search(r'/(\d{4})/(\d{2})/(\d{2})/', link)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    return None


def _find_html_for_post(link, slug):
    """Given a post link and slug, find the matching saved HTML page."""
    html_dir = MIRROR_DIR / "html"
    slug_clean = slug.replace("/", "_")

    # Direct flat match
    candidates = sorted(html_dir.glob(f"{slug_clean}.html"))
    if candidates:
        return candidates[0]

    # Derive path from link URL
    if link:
        from urllib.parse import urlparse
        parsed = urlparse(link)
        path_str = parsed.path.rstrip("/") or "/"
        # Try derived path
        rel = path_str.lstrip("/")
        candidate = html_dir / f"{rel}.html"
        if candidate.is_file():
            return candidate
        # Try nested from link segments
        leaf = link.rstrip("/").rsplit("/", 1)[-1]
        candidates = sorted(list(html_dir.rglob(f"{leaf}.html")))
        if candidates:
            return candidates[0]

    # Fallback: recursive glob
    candidates = sorted(html_dir.rglob(f"{slug_clean}.html"))
    if candidates:
        return candidates[0]
    candidates = sorted(html_dir.rglob(f"{slug_clean}*.html"))
    return candidates[0] if candidates else None


def generate_time_reference_table():
    """Build a comprehensive time reference table for all published posts/pages."""
    api_base = MIRROR_DIR / "api" / "wp-json" / "wp" / "v2"
    rows = []

    def collect(endpoint_dir, content_type):
        d = api_base / endpoint_dir
        if not d.is_dir():
            return
        for jf in sorted(d.glob("*.json")):
            try:
                data = json.loads(jf.read_bytes())
            except (json.JSONDecodeError, ValueError):
                continue
            if not isinstance(data, dict) or "id" not in data:
                continue
            pid = data["id"]
            slug = data.get("slug", "")
            link = data.get("link", "")
            api_date = data.get("date", "")
            api_date_gmt = data.get("date_gmt", "")
            api_modified = data.get("modified", "")
            api_modified_gmt = data.get("modified_gmt", "")

            url_date = _parse_url_date(link)

            # Attempt to parse the HTML page for additional timestamps
            html_path = _find_html_for_post(link, slug)
            html_text = html_path.read_text(encoding="utf-8", errors="replace") if html_path else ""

            visible_date = _parse_visible_date(html_text)
            meta_published = _parse_meta_time(html_text, "article:published_time")
            meta_modified = _parse_meta_time(html_text, "article:modified_time")
            epochs = _extract_epoch_timestamps(html_text)
            epoch_strs = "; ".join(
                f"{kind}={_epoch_to_str(ts)}" for kind, ts in sorted(set(epochs))
            ) if epochs else ""

            rows.append({
                "id": pid,
                "type": content_type,
                "slug": slug,
                "url_date": url_date or "",
                "api_date": api_date,
                "api_date_gmt": api_date_gmt,
                "api_modified": api_modified,
                "api_modified_gmt": api_modified_gmt,
                "visible_date": visible_date or "",
                "meta_published": meta_published or "",
                "meta_modified": meta_modified or "",
                "epochs": epoch_strs,
            })

    collect("posts", "post")
    collect("pages", "page")

    if not rows:
        return ""

    # Sort by the time a viewer would have seen it: use date_gmt (publish time)
    def sort_key(r):
        for field in ["api_date_gmt", "meta_published", "visible_date", "api_date", "url_date"]:
            v = r.get(field, "")
            if v:
                return v
        return ""

    rows.sort(key=sort_key)

    # Build the table
    lines = [
        "",
        "---",
        "",
        "# Post/Page Time Reference Analysis",
        "",
        "**Generated:** " + datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
        "**Source:** " + BASE_URL + "/wp-json/wp/v2/{posts,pages}",
        "",
        "Every available time reference for each published post/page, sorted by the time",
        "a viewer would have first seen it on the website (publish date).",
        "",
        "### Key",
        "",
        "| Column | Source | Description |",
        "|--------|--------|-------------|",
        "| **ID** | REST API | Post/page numeric ID |",
        "| **Type** | REST API | `post` or `page` |",
        "| **Slug** | REST API | URL slug |",
        "| **URL-date** | Permalink | Date extracted from the post URL |",
        "| **API date** | REST API `date` | Creation date (site timezone) |",
        "| **API date (GMT)** | REST API `date_gmt` | Creation date (UTC) - **canonical publish time** |",
        "| **API modified** | REST API `modified` | Last modified (site timezone) |",
        "| **API modified (GMT)** | REST API `modified_gmt` | Last modified (UTC) |",
        "| **Visible date** | HTML `<time>` | Date shown to visitors on the page |",
        "| **Meta published** | HTML `<meta property>` | Open Graph published_time |",
        "| **Meta modified** | HTML `<meta property>` | Open Graph modified_time |",
        "| **Epoch timestamps** | Page content | Unix epoch timestamps embedded in log content (memory_bloc / exif_created) |",
        "",
        "### Time Reference Table",
        "",
        "| ID | Type | Slug | URL-date | API date | API date (GMT) | API modified | API modified (GMT) | Visible date | Meta published | Meta modified | Epoch timestamps |",
        "|----|------|------|----------|----------|----------------|--------------|--------------------|--------------|----------------|---------------|------------------|",
    ]

    for r in rows:
        lines.append(
            f"| {r['id']} | {r['type']} | {r['slug']} "
            f"| {r['url_date']} | {r['api_date']} | {r['api_date_gmt']} "
            f"| {r['api_modified']} | {r['api_modified_gmt']} "
            f"| {r['visible_date']} | {r['meta_published']} | {r['meta_modified']} "
            f"| {r['epochs']} |"
        )

    lines.append("")
    lines.append("*Auto-generated by update_mirror.py*")
    lines.append("")
    return "\n".join(lines)


# --- Main ---

def clean_stale_paths():
    """Remove files that exist where we need directories (and vice versa)."""
    conflicts = [
        MIRROR_DIR / "api" / "wp-json" / "oembed" / "1.0",          # file, needs to be dir
        MIRROR_DIR / "api" / "wp-json" / "oembed" / "1.0_dir",       # stale backup
    ]
    for c in conflicts:
        if c.is_file():
            c.unlink()
        elif c.is_dir():
            import shutil
            shutil.rmtree(c)

    # Remove stale flat HTML duplicates that also exist in nested form
    # Old url_to_path stored /2026/05/25/sec-log-113610/ as
    # html/2026_05_25_sec-log-113610.html. Current code stores it as
    # html/2026/05/25/sec-log-113610.html. Clean up the old flat copies.
    html_dir = MIRROR_DIR / "html"
    if html_dir.exists():
        for f in sorted(html_dir.glob("*.html")):
            name = f.stem
            if "_" not in name:
                continue
            # Build the nested equivalent path
            nested = html_dir / (name.replace("_", "/") + ".html")
            if nested.is_file() and nested.stat().st_size >= f.stat().st_size * 0.9:
                # Nested copy exists and is roughly same size - remove flat stale
                f.unlink()
                hdr = f.parent / (f.name + ".headers.json")
                if hdr.is_file():
                    hdr.unlink()
                print(f"  CLEANED stale flat: {f.name}")


def main():
    log("=" * 60)
    log("  project-skyscraper.com - Complete Mirror Update")
    log(f"  {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")
    log("  Fully self-discovering - no hardcoded values")
    log("=" * 60)
    log("")

    clean_stale_paths()
    generate_unresolved_markers()
    _migrate_old_reports(MIRROR_DIR / "diffs")

    # Phase 1: Discovery
    log("PHASE 1: Discovery")
    sitemap_urls = discover_sitemap_urls()
    log(f"  {len(sitemap_urls)} URLs from sitemaps")
    log("")

    # Phase 2: HTML pages
    log("PHASE 2: HTML Pages")
    fetch_html_pages(sitemap_urls)
    log("")

    # Phase 3: REST API (returns discovered IDs and links)
    log("PHASE 3: REST API")
    post_ids, page_ids, media_ids, post_links, page_links, unpublished_posts, unpublished_pages = fetch_api_endpoints()
    log("")

    # Phase 4: Media
    log("PHASE 4: Media")
    fetch_media(post_links, page_links, sitemap_urls)
    log("")

    # Phase 5: Password-protected pages
    log("PHASE 5: Password-Protected Pages")
    fetch_password_protected_pages()
    log("")

    # Phase 6: Theme Assets
    log("PHASE 6: Theme Assets")
    fetch_theme_assets()
    log("")

    # Phase 7: Plugin Assets
    log("PHASE 7: Plugin Assets")
    fetch_plugin_assets()
    log("")

    # Phase 8: Discovery
    log("PHASE 8: Discovery Documents")
    fetch_discovery()
    log("")

    # Phase 9: Extras
    log("PHASE 9: Extras")
    fetch_extras()
    log("")

    # Phase 10: Third-party CDN
    log("PHASE 10: Third-party CDN")
    fetch_third_party()
    log("")

    # Phase 11: External references
    log("PHASE 11: External References")
    fetch_external_references()
    log("")

    # Phase 12: Additional endpoint probes
    log("PHASE 12: Additional Endpoints")
    fetch_additional_endpoints()
    log("")

    # Phase 13: Manifest & Diff
    log("PHASE 12: Manifest & Diff")
    generate_manifest()
    generate_id_series_analysis()
    time_table = generate_time_reference_table()
    if time_table:
        series_path = MIRROR_DIR / "POST_ID_SERIES.md"
        existing = series_path.read_text(encoding="utf-8") if series_path.is_file() else ""
        series_path.write_text(existing.rstrip() + "\n\n" + time_table, encoding="utf-8")
        log(f"  Time reference table appended to POST_ID_SERIES.md")
    generate_unpublished_report(unpublished_posts, unpublished_pages)
    _migrate_diffs()
    store_diff()
    # Clean up old dated change reports - now using single CHANGELOG.md
    diff_dir = MIRROR_DIR / "diffs"
    for old_report in diff_dir.glob("change_*.md"):
        try:
            old_report.unlink()
            log(f"  CLEANED old report: {old_report.name}")
        except OSError:
            pass
    log("")

    # Cleanup: remove __pycache__ directories
    for cache_dir in MIRROR_DIR.rglob("__pycache__"):
        if cache_dir.is_dir():
            import shutil
            shutil.rmtree(cache_dir)
            log(f"  CLEANED {cache_dir.relative_to(MIRROR_DIR)}")

    log("=" * 60)
    log("  UPDATE COMPLETE")
    log(f"  Fetched: {stats['fetched']}  |  New: {stats['new']}  |  Changed: {stats['changed']}")
    log(f"  Skipped (unchanged): {stats['skipped']}  |  Failed: {stats['failed']}")
    if changes:
        log(f"  Changes detected: {len(changes)} file(s) - see diffs/CHANGELOG.md")
    log("=" * 60)


if __name__ == "__main__":
    main()
