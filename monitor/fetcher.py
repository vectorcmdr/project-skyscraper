"""Full mirror fetcher -- one-shot complete site mirror with diff generation.

Replaces the old update_mirror.py with modular, clean architecture.
"""

import hashlib
import json
import os
import re
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

from monitor.config import (
    BASE_URL, MIRROR_DIR, DIFF_DIR, PAGE_PASSWORD, PASSWORD_PROTECTED_PAGES,
    MAX_WORKERS,
)
from monitor.http_client import fetch as http_fetch, jitter
from monitor.url_mapper import url_to_path, is_binary_url
from monitor.logger import log
from monitor.beautifier import beautify
from monitor.noise_filter import diff_has_real_changes
from monitor.diff_engine import compute_diff, build_diff_header
from monitor.report_writer import (
    generate_manifest_report, generate_unpublished_report, write_changelog,
)
from monitor.discovery import discover_sitemap_urls, fetch_and_save, discover_rest_api, KNOWN_NS_ROOTS, JETPACK_SUBS

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 project-skyscraper-mirror/1.0"

_stats = {"fetched": 0, "skipped": 0, "failed": 0, "changed": 0, "new": 0}
_changes = []


def full_fetch():
    global _stats, _changes
    _stats = {"fetched": 0, "skipped": 0, "failed": 0, "changed": 0, "new": 0}
    _changes = []

    log("=" * 60)
    log("  project-skyscraper.com - Complete Mirror Update")
    log(f"  {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")
    log("=" * 60)
    log("")

    _clean_stale_paths()
    _generate_unresolved_markers()

    log("PHASE 1: Discovery")
    sitemap_urls = discover_sitemap_urls()
    log(f"  {len(sitemap_urls)} URLs from sitemaps")
    log("")

    log("PHASE 2: HTML Pages")
    _fetch_html_pages(sitemap_urls)
    log("")

    log("PHASE 3: REST API")
    post_ids, page_ids, media_ids, post_links, page_links, unpub_posts, unpub_pages = _fetch_api_endpoints()
    log("")

    log("PHASE 4: Media")
    _fetch_media(post_links, page_links, sitemap_urls)
    log("")

    log("PHASE 5: Password-Protected Pages")
    _fetch_password_protected_pages()
    log("")

    log("PHASE 6: Theme Assets")
    _fetch_theme_assets()
    log("")

    log("PHASE 7: Plugin Assets")
    _fetch_plugin_assets()
    log("")

    log("PHASE 8: Discovery Documents")
    _fetch_discovery()
    log("")

    log("PHASE 9: Extras")
    _fetch_extras()
    log("")

    log("PHASE 10: Third-party CDN")
    _fetch_third_party()
    log("")

    log("PHASE 11: External References")
    _fetch_external_references()
    log("")

    log("PHASE 12: Additional Endpoints")
    _fetch_additional_endpoints()
    log("")

    log("PHASE 13: Reports")
    _generate_reports(unpub_posts, unpub_pages)
    log("")

    _clean_cache()

    log("=" * 60)
    log("  UPDATE COMPLETE")
    log(f"  Fetched: {_stats['fetched']}  New: {_stats['new']}  Changed: {_stats['changed']}")
    log(f"  Skipped: {_stats['skipped']}  Failed: {_stats['failed']}")
    if _changes:
        log(f"  Changes: {len(_changes)} file(s)")
    log("=" * 60)


def _fetch_save(url: str, subdir: str = "", save_headers: bool = False,
                headers_extra: dict = None, cookie: str = None) -> tuple:
    path = url_to_path(url, subdir=subdir)
    path.parent.mkdir(parents=True, exist_ok=True)

    old_bytes = path.read_bytes() if path.is_file() else None
    old_hash = hashlib.md5(old_bytes).hexdigest() if old_bytes is not None else None

    req_headers = {"User-Agent": USER_AGENT, "Accept": "*/*", **(headers_extra or {})}
    if cookie:
        req_headers["Cookie"] = cookie

    req = urllib.request.Request(url, headers=req_headers)
    try:
        import urllib.error
        resp = urllib.request.urlopen(req, timeout=30)
        content = resp.read()
        code = resp.status
    except urllib.error.HTTPError as e:
        _stats["failed"] += 1
        try:
            content = e.read()
            path.write_bytes(content)
            log(f"  ERR  {url} -> {e.code}")
        except Exception:
            pass
        return ("error", path, e.code, b"")
    except Exception as e:
        _stats["failed"] += 1
        log(f"  FAIL {url} -> {e}")
        return ("error", path, 0, b"")

    new_hash = hashlib.md5(content).hexdigest()
    if old_hash == new_hash:
        _stats["skipped"] += 1
        return ("skipped", path, code, content)

    if old_hash is not None and old_bytes:
        _save_diff_for_file(url, path, old_bytes, content)

    path.write_bytes(content)
    if old_hash is None:
        _stats["new"] += 1
        log(f"  NEW  {url}")
    else:
        _stats["changed"] += 1
        log(f"  CHG  {url}")
        _changes.append((url, path))
    _stats["fetched"] += 1

    if save_headers:
        hdr = path.parent / (path.name + ".headers.json")
        hdr.write_text(json.dumps(dict(resp.headers.items()), indent=2, default=str))

    return ("ok", path, code, content)


def _save_diff_for_file(url: str, path: Path, old_bytes: bytes, new_bytes: bytes):
    diff_dir = DIFF_DIR
    diff_dir.mkdir(parents=True, exist_ok=True)
    rel = str(path.relative_to(MIRROR_DIR)).replace("\\", "/")
    safe_name = re.sub(r'[^a-zA-Z0-9_\-.]', '_', rel) + ".diff"

    if is_binary_url(url):
        diff_path = diff_dir / safe_name
        diff_path.write_text(
            f"# Diff: {url}\n# File: {rel}\n"
            f"# Timestamp: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}\n"
            f"# Binary: size changed\n\n"
            f"--- old/{rel}\n+++ new/{rel}\n"
            f"-{_fmt_size(len(old_bytes))}\n+{_fmt_size(len(new_bytes))}\n",
            encoding="utf-8"
        )
        log(f"    DIFF saved: {safe_name} (size change)")
        return

    diff_text = compute_diff(old_bytes, new_bytes, url, str(path))
    if diff_text is None:
        return

    header = build_diff_header(url, rel, len(old_bytes), len(new_bytes))
    diff_path = diff_dir / safe_name
    diff_path.write_text(header + diff_text, encoding="utf-8")
    log(f"    DIFF saved: {safe_name}")


def _format_stats():
    return {
        "fetched": _stats["fetched"],
        "skipped": _stats["skipped"],
        "failed": _stats["failed"],
        "changed": _stats["changed"],
        "new": _stats["new"],
    }


def _fetch_html_pages(sitemap_urls: dict):
    log("=== FETCHING HTML PAGES ===")
    for url in sitemap_urls:
        if url.startswith(BASE_URL) and sitemap_urls[url] == "page":
            _fetch_save(url, "html", save_headers=True)
            time.sleep(0.3)
    _fetch_save(BASE_URL, "html", save_headers=True)


def _fetch_api_endpoints():
    log("--- Root & Route Discovery ---")
    namespaces, route_keys = discover_rest_api()

    for ns_root in sorted(KNOWN_NS_ROOTS):
        _fetch_save(f"{BASE_URL}{ns_root}", "api")
        time.sleep(0.15)

    log("--- wp/v2 Collection Endpoints ---")
    collection_endpoints = [
        "/wp-json/wp/v2/posts", "/wp-json/wp/v2/pages",
        "/wp-json/wp/v2/media", "/wp-json/wp/v2/categories",
        "/wp-json/wp/v2/tags", "/wp-json/wp/v2/types",
        "/wp-json/wp/v2/statuses", "/wp-json/wp/v2/taxonomies",
        "/wp-json/wp/v2/users", "/wp-json/wp/v2/comments",
        "/wp-json/wp/v2/blocks", "/wp-json/wp/v2/navigation",
        "/wp-json/wp/v2/search",
    ]
    for ep in collection_endpoints:
        _fetch_save(f"{BASE_URL}{ep}", "api")
        time.sleep(0.15)
        _fetch_save(f"{BASE_URL}{ep}?per_page=100", "api")
        time.sleep(0.1)

    log("--- Auth-gated Endpoints ---")
    for ep in ["/wp-json/wp/v2/settings", "/wp-json/wp/v2/themes",
               "/wp-json/wp/v2/plugins", "/wp-json/wp/v2/block-types",
               "/wp-json/wp/v2/templates", "/wp-json/wp/v2/template-parts",
               "/wp-json/wp/v2/global-styles", "/wp-json/wp/v2/menu-items",
               "/wp-json/wp/v2/menus", "/wp-json/wp/v2/sidebars",
               "/wp-json/wp/v2/widgets"]:
        _fetch_save(f"{BASE_URL}{ep}", "api")
        time.sleep(0.1)

    log("--- Individual Items ---")
    def _fetch_all_individual(list_ep, item_tmpl):
        items = _discover_list(list_ep)
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
            _fetch_save(f"{BASE_URL}{item_tmpl.format(id=iid)}", "api")
            time.sleep(0.1)
        return ids, urls

    post_ids, post_links = _fetch_all_individual("/wp-json/wp/v2/posts", "/wp-json/wp/v2/posts/{id}")
    page_ids, page_links = _fetch_all_individual("/wp-json/wp/v2/pages", "/wp-json/wp/v2/pages/{id}")
    media_ids, _ = _fetch_all_individual("/wp-json/wp/v2/media", "/wp-json/wp/v2/media/{id}")

    log(f"    Posts: {len(post_ids)}  Pages: {len(page_ids)}  Media: {len(media_ids)}")

    unpub_posts, unpub_pages = _probe_unpublished_ids(post_ids + page_ids + media_ids)

    log("--- Jetpack Sub-endpoints ---")
    for ep in JETPACK_SUBS:
        _fetch_save(f"{BASE_URL}{ep}", "api")
        time.sleep(0.12)

    log("--- WP.com Sub-endpoints ---")
    for ep in ["/wp-json/wpcom/v2/sites", "/wp-json/wpcom/v2/site-verticals",
               "/wp-json/wpcom/v2/block-likes"]:
        _fetch_save(f"{BASE_URL}{ep}", "api")
        time.sleep(0.12)

    log("--- oEmbed Endpoints ---")
    oembed_urls = {BASE_URL}
    for surl in discover_sitemap_urls():
        if surl.startswith(BASE_URL):
            oembed_urls.add(surl)
    for link in post_links + page_links:
        oembed_urls.add(link)
    for ou in sorted(oembed_urls):
        encoded = urllib.parse.quote(ou, safe="")
        for fmt in ["", "&format=xml"]:
            _fetch_save(f"{BASE_URL}/wp-json/oembed/1.0/embed?url={encoded}{fmt}", "api")
            time.sleep(0.08)

    _fetch_save(f"{BASE_URL}/?rest_route=/", "api")
    _fetch_save(f"{BASE_URL}/?rest_route=/wp/v2", "api")

    return post_ids, page_ids, media_ids, post_links, page_links, unpub_posts, unpub_pages


def _probe_unpublished_ids(all_known_ids: list) -> tuple:
    if not all_known_ids:
        return [], []

    max_id = max(all_known_ids)
    scan_max = max_id + 300

    log(f"  Probing IDs 1-{scan_max} for unpublished content...")

    unpublished_posts = []
    unpublished_pages = []
    import threading
    _up_lock = threading.Lock()

    def _check_one(pid):
        for ep, lst in [("/posts", unpublished_posts), ("/pages", unpublished_pages)]:
            url = f"{BASE_URL}/wp-json/wp/v2{ep}/{pid}"
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "*/*"})
            try:
                urllib.request.urlopen(req, timeout=15)
                return
            except urllib.error.HTTPError as e:
                if e.code in (401, 403):
                    with _up_lock:
                        lst.append((pid, e.code))
                return
            except Exception:
                return

    all_probe = list(range(1, scan_max + 1))
    done = [0]
    _prog_lock = threading.Lock()

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futs = {ex.submit(_check_one, pid): pid for pid in all_probe}
        for f in as_completed(futs):
            with _prog_lock:
                done[0] += 1
                if done[0] % 200 == 0:
                    log(f"    Progress: {done[0]}/{len(all_probe)} ...")

    log(f"    Found {len(unpublished_posts)} unpublished posts, {len(unpublished_pages)} unpublished pages")
    return unpublished_posts, unpublished_pages


def _discover_list(endpoint: str) -> list:
    for suffix in [f"{endpoint}?per_page=100", f"{endpoint}?per_page=50", endpoint]:
        result = _fetch_save(f"{BASE_URL}{suffix}", "api")
        if result[0] != "error" and result[3]:
            try:
                data = json.loads(result[3])
                if isinstance(data, list):
                    return data
            except json.JSONDecodeError:
                pass
    return []


def _fetch_media(post_links, page_links, sitemap_urls):
    log("=== FETCHING MEDIA FILES ===")
    media_urls = set()

    for url, typ in sitemap_urls.items():
        if typ == "image":
            media_urls.add(url)

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

    html_dir = MIRROR_DIR / "html"
    if html_dir.exists():
        for hf in html_dir.rglob("*.html"):
            text = hf.read_text(encoding="utf-8", errors="replace")
            for m in re.finditer(r'''(?:src|href|data-src)="([^"]+)"''', text):
                u = m.group(1)
                if "/wp-content/uploads/" in u:
                    clean = u.split("?")[0]
                    if clean.startswith("//"):
                        clean = "https:" + clean
                    if clean.startswith("http"):
                        media_urls.add(clean)

    log(f"  Discovered {len(media_urls)} media URLs")
    for url in sorted(media_urls):
        if url.startswith(("http://", "https://")):
            _fetch_save(url, "media")
            time.sleep(0.15)

    for jf in sorted(api_dir.rglob("wp-json/wp/v2/media/*.json")):
        try:
            data = json.loads(jf.read_bytes())
            if isinstance(data, dict):
                src = data.get("source_url", "")
                if src and "wp-content/uploads" in src:
                    base = src.rsplit(".", 1)[0]
                    ext = src.rsplit(".", 1)[-1]
                    for suffix in ["-150x150", "-300x300", "-768x768", "-1024x1024"]:
                        thumb = f"{base}{suffix}.{ext}"
                        _fetch_save(thumb, "media")
                        time.sleep(0.08)
        except (json.JSONDecodeError, ValueError):
            pass


def _fetch_password_protected_pages():
    log("=== FETCHING PASSWORD-PROTECTED PAGES ===")
    cookies = _get_postpass_cookie()
    if not cookies:
        log("  WARN: No postpass cookie obtained")
        return
    cookie_header = "; ".join(f"{k}={v}" for k, v in cookies.items())
    for url in PASSWORD_PROTECTED_PAGES:
        _fetch_save(url, "html", cookie=cookie_header)
        time.sleep(0.5)


def _get_postpass_cookie() -> dict:
    url = f"{BASE_URL}/wp-login.php?action=postpass"
    data = urllib.parse.urlencode({
        "post_password": PAGE_PASSWORD,
        "Submit": "Enter",
        "redirect_to": PASSWORD_PROTECTED_PAGES[0],
    }).encode()

    class NoRedirectHandler(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None

    opener = urllib.request.build_opener(NoRedirectHandler)
    req = urllib.request.Request(url, data=data, headers={
        "User-Agent": USER_AGENT,
        "Content-Type": "application/x-www-form-urlencoded",
    })
    try:
        resp = opener.open(req, timeout=30)
    except urllib.error.HTTPError as e:
        resp = e

    cookies = {}
    raw = resp.headers.get_all("Set-Cookie") if hasattr(resp.headers, "get_all") else None
    if raw is None:
        set_cookie = resp.headers.get("Set-Cookie", "")
        raw = re.split(r', (?=[a-zA-Z0-9_\-]+=)', set_cookie) if set_cookie else []
    for part in raw:
        part = part.strip()
        m = re.search(r'(wp-postpass_[a-f0-9]+)=([^;]+)', part)
        if m:
            cookies[m.group(1)] = urllib.parse.unquote(m.group(2))
            log(f"  Got postpass cookie: {m.group(1)}")
    return cookies


def _fetch_theme_assets():
    log("=== FETCHING THEME ASSETS ===")
    theme_base = f"{BASE_URL}/wp-content/themes/perenne"
    for tp in [
        "/style.css", "/theme.json", "/readme.txt", "/screenshot.png",
        "/index.php", "/functions.php", "/header.php", "/footer.php",
        "/assets/css/main.css", "/assets/js/navigation.js",
        "/assets/fonts/ibm-plex-mono_normal_400.ttf",
        "/assets/fonts/ibm-plex-mono_italic_400.ttf",
    ]:
        _fetch_save(f"{theme_base}{tp}", "assets")
        time.sleep(0.12)

    html_resources = _extract_html_resource_urls()
    for u in html_resources:
        if "/wp-content/themes/" in u:
            _fetch_save(u, "assets")
            time.sleep(0.1)


def _fetch_plugin_assets():
    log("=== FETCHING PLUGIN ASSETS ===")
    plugin_probes = [
        "jetpack/modules/related-posts/related-posts.css",
        "jetpack/modules/likes/style.css",
        "jetpack/_inc/build/likes/style.min.css",
        "gutenberg/build/scripts/dom-ready/index.min.js",
        "gutenberg/build/styles/block-library/paragraph/style.min.css",
        "wp-statistics/assets/js/tracker.js",
    ]
    for pp in plugin_probes:
        _fetch_save(f"{BASE_URL}/wp-content/plugins/{pp}", "assets")
        time.sleep(0.1)

    html_resources = _extract_html_resource_urls()
    for u in html_resources:
        if "/wp-content/plugins/" in u:
            _fetch_save(u, "assets")
            time.sleep(0.1)


def _fetch_discovery():
    log("=== FETCHING DISCOVERY DOCUMENTS ===")
    for path in ["/robots.txt", "/sitemap.xml", "/sitemap-1.xml",
                 "/sitemap.xsl", "/sitemap-index.xsl"]:
        _fetch_save(f"{BASE_URL}{path}", "discovery")
        time.sleep(0.1)


def _fetch_extras():
    log("=== FETCHING EXTRAS ===")
    extras = [
        (f"{BASE_URL}/readme.html", "extras"),
        (f"{BASE_URL}/license.txt", "extras"),
        (f"{BASE_URL}/favicon.ico", "extras"),
        (f"{BASE_URL}/xmlrpc.php", "extras"),
        (f"{BASE_URL}/wp-admin/css/install.css", "extras"),
    ]
    for url, subdir in extras:
        _fetch_save(url, subdir=subdir, save_headers=True)
        time.sleep(0.12)


def _fetch_third_party():
    log("=== FETCHING THIRD-PARTY CDN ASSETS ===")
    for url in [
        "https://s0.wp.com/wp-content/js/bilmur.min.js?m=202622",
        "https://stats.wp.com/e-202622.js",
    ]:
        _fetch_save(url, "third_party")
        time.sleep(0.2)

    html_resources = _extract_html_resource_urls()
    font_urls = {u for u in html_resources if "fonts." in u or ".woff2" in u or ".woff" in u}
    if not font_urls:
        for u in [
            "https://fonts.wp.com/s/ibmplexmono/v19/-F63fjptAgt5VM-kVkqdyU8n5i0g1l9kn-s.woff2",
            "https://fonts.wp.com/s/ibmplexmono/v19/-F6pfjptAgt5VM-kVkqdyU8n1ioq131hj-sNFQ.woff2",
        ]:
            font_urls.add(u)
    for url in sorted(font_urls):
        _fetch_save(url, "third_party")
        time.sleep(0.12)


def _fetch_external_references():
    log("=== FETCHING EXTERNAL REFERENCES ===")
    refs = [
        "https://www.reddit.com/r/NoMansSkyTheGame/comments/1tczflq/connection_detected_access_denied.json",
        "https://forums.atlas-65.com/t/project-skyscraper-no-mans-sky-arg/9095/180.json",
    ]
    for url in refs:
        try:
            r = urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": USER_AGENT}), timeout=15)
            content = r.read()
            path = url_to_path(url, subdir="endpoints")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
            _stats["fetched"] += 1
            log(f"  OK   {url}")
        except Exception as e:
            _stats["failed"] += 1
            log(f"  FAIL {url} -> {e}")
        time.sleep(0.5)


def _fetch_additional_endpoints():
    log("=== FETCHING ADDITIONAL ENDPOINTS ===")
    for path in ["/wp-content/debug.log", "/wp-admin/"]:
        _fetch_save(f"{BASE_URL}{path}", "endpoints")
        time.sleep(0.12)


def _generate_reports(unpub_posts, unpub_pages):
    log("=== GENERATING REPORTS ===")
    total_files = 0
    total_size = 0
    section_data = {}

    for root, dirs, files in os.walk(str(MIRROR_DIR)):
        dirs[:] = [d for d in dirs if d not in (".git", "__pycache__", "state", "monitor_reports")]
        for fn in files:
            fp = os.path.join(root, fn)
            rel = os.path.relpath(fp, str(MIRROR_DIR))
            sz = os.path.getsize(fp)
            total_files += 1
            total_size += sz
            section = rel.replace("\\", "/").split("/")[0]
            section_data.setdefault(section, {"files": 0, "size": 0})
            section_data[section]["files"] += 1
            section_data[section]["size"] += sz

    generate_manifest_report(section_data, total_files, total_size)
    generate_unpublished_report(unpub_posts, unpub_pages)

    meaningful_changes = []
    for url, path in _changes:
        rel = str(path.relative_to(MIRROR_DIR)).replace("\\", "/")
        safe_name = re.sub(r'[^a-zA-Z0-9_\-.]', '_', rel) + ".diff"
        diff_file = DIFF_DIR / safe_name
        if diff_file.is_file():
            content = diff_file.read_text(encoding="utf-8")
            meaningful_changes.append((url, path, content, rel))

    if meaningful_changes:
        write_changelog(meaningful_changes)


def _extract_html_resource_urls() -> set:
    found = set()
    html_dir = MIRROR_DIR / "html"
    if not html_dir.exists():
        return found
    patterns = [
        re.compile(r'''(?:src|href|data-src|content)="([^"]+)"'''),
        re.compile(r'''url\(['"]?([^'")\s]+)['"]?\)'''),
    ]
    for hf in sorted(html_dir.rglob("*.html")):
        text = hf.read_text(encoding="utf-8", errors="replace")
        for pat in patterns:
            for m in pat.finditer(text):
                u = m.group(1)
                if u.startswith("//"):
                    u = "https:" + u
                if u.startswith(("http://", "https://")) or u.startswith("/"):
                    found.add(u)
    return found


def _clean_stale_paths():
    conflicts = [
        MIRROR_DIR / "api" / "wp-json" / "oembed" / "1.0",
        MIRROR_DIR / "api" / "wp-json" / "oembed" / "1.0_dir",
    ]
    for c in conflicts:
        if c.is_file():
            c.unlink()
        elif c.is_dir():
            import shutil
            shutil.rmtree(c)

    html_dir = MIRROR_DIR / "html"
    if html_dir.exists():
        for f in sorted(html_dir.glob("*.html")):
            name = f.stem
            if "_" not in name:
                continue
            nested = html_dir / (name.replace("_", "/") + ".html")
            if nested.is_file() and nested.stat().st_size >= f.stat().st_size * 0.9:
                f.unlink()
                hdr = f.parent / (f.name + ".headers.json")
                if hdr.is_file():
                    hdr.unlink()


NS_ROOTS_DIRS = [
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


def _generate_unresolved_markers():
    api_dir = MIRROR_DIR / "api"
    for ns in NS_ROOTS_DIRS:
        unresolved = api_dir / (ns + ".unresolved")
        if unresolved.is_file():
            continue
        unresolved.parent.mkdir(parents=True, exist_ok=True)
        unresolved.write_text(
            f"Unfetchable endpoint: /{ns}\n\n"
            "This is a namespace root in the WordPress REST API.\n"
            "It cannot be fetched directly because multiple endpoints live under this path.\n\n"
            "Generated by monitor\n"
        )


def _clean_cache():
    for cache_dir in MIRROR_DIR.rglob("__pycache__"):
        if cache_dir.is_dir():
            import shutil
            shutil.rmtree(cache_dir)


def _fmt_size(bytes_val):
    for unit in ("B", "KB", "MB", "GB"):
        if bytes_val < 1024:
            return f"{bytes_val:.1f} {unit}"
        bytes_val /= 1024
    return f"{bytes_val:.1f} TB"
