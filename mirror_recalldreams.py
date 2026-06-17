"""Mirror script for recalldreams.dev — full fetch with diffing, noise filtering, beautification."""

import hashlib
import json
import re
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from monitor.diff_engine import compute_diff, build_diff_header
from monitor.url_mapper import is_binary_url

MIRROR_DIR = Path(__file__).parent.resolve()
RECALL_DIR = MIRROR_DIR / "mirrors" / "recalldreams"
BASE_URL = "https://recalldreams.dev"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 project-skyscraper-mirror/1.0"

_stats = {"fetched": 0, "skipped": 0, "failed": 0, "changed": 0, "new": 0}
_changes = []


def log(msg: str, tag: str = "MIRROR"):
    print(f"  [{tag}] {msg}", flush=True)


def _save_diff(url: str, path: Path, old_bytes: bytes, new_bytes: bytes) -> str | None:
    diff_dir = RECALL_DIR / "diffs"
    diff_dir.mkdir(parents=True, exist_ok=True)
    rel = str(path.relative_to(RECALL_DIR)).replace("\\", "/")
    safe_name = re.sub(r'[^a-zA-Z0-9_\-.]', '_', rel) + ".diff"

    if is_binary_url(url):
        diff_path = diff_dir / safe_name
        result = (
            f"# Diff: {url}\n# File: {rel}\n"
            f"# Timestamp: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}\n"
            f"# Binary: size changed\n\n"
            f"--- old/{rel}\n+++ new/{rel}\n"
            f"-{_fmt_size(len(old_bytes))}\n+{_fmt_size(len(new_bytes))}\n"
        )
        diff_path.write_text(result, encoding="utf-8")
        log(f"  DIFF saved: {safe_name} (size change)", "DIFF")
        return None

    diff_text = compute_diff(old_bytes, new_bytes, url, str(path))
    if diff_text is None:
        return None

    header = build_diff_header(url, rel, len(old_bytes), len(new_bytes))
    full_text = header + diff_text
    diff_path = diff_dir / safe_name
    diff_path.write_text(full_text, encoding="utf-8")
    log(f"  DIFF saved: {safe_name}", "DIFF")
    return full_text


def _fmt_size(size: int) -> str:
    if size < 1024:
        return f"{size} B"
    elif size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size / (1024 * 1024):.2f} MB"


def _fetch_save(url: str, subdir: str) -> tuple:
    path = RECALL_DIR / subdir / _url_to_relpath(url)
    path.parent.mkdir(parents=True, exist_ok=True)

    old_bytes = path.read_bytes() if path.is_file() else None
    old_hash = hashlib.md5(old_bytes).hexdigest() if old_bytes else None

    req = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT,
        "Accept": "*/*",
        "Cache-Control": "no-cache",
    })
    try:
        resp = urllib.request.urlopen(req, timeout=30)
        content = resp.read()
        code = resp.status
    except urllib.error.HTTPError as e:
        _stats["failed"] += 1
        try:
            content = e.read()
            path.write_bytes(content)
            log(f"ERR  {url} -> {e.code}")
        except Exception:
            pass
        return ("error", path, e.code, b"")
    except Exception as e:
        _stats["failed"] += 1
        log(f"FAIL  {url} -> {e}")
        return ("error", path, 0, b"")

    new_hash = hashlib.md5(content).hexdigest()
    if old_hash == new_hash:
        _stats["skipped"] += 1
        return ("skipped", path, code, content)

    if old_bytes is not None:
        diff_text = _save_diff(url, path, old_bytes, content)
        if diff_text is not None:
            _changes.append({
                "type": "external_content_changed",
                "site": "recalldreams.dev",
                "site_label": "recalldreams",
                "url": url,
                "diff": diff_text,
                "detail": f"Content changed: {url}",
            })

    path.write_bytes(content)
    if old_hash is None:
        _stats["new"] += 1
        log(f"NEW  {url}")
    else:
        _stats["changed"] += 1
        log(f"CHG  {url}")
    _stats["fetched"] += 1
    return ("ok", path, code, content)


def _url_to_relpath(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    path_str = parsed.path.rstrip("/") or "/"
    q = parsed.query
    if q:
        qs = q.replace("&", "_").replace("=", "_").replace("%", "").replace(";", "_")
        path_str = path_str + "_" + qs[:120]
    if path_str.endswith("/") or not Path(path_str).suffix:
        suffix = ".json" if ("wp-json" in url or "oembed" in url) else "index.html"
        path_str = path_str.rstrip("/") + "/" + suffix
    path_str = path_str.replace("https:", "").replace("http:", "")
    while path_str.startswith("/"):
        path_str = path_str[1:]
    path_str = re.sub(r'[<>:"\\|?*]', "_", path_str)
    parts = [p[:200] for p in path_str.replace("\\", "/").split("/")]
    return "/".join(parts)


def _fetch_json(endpoint: str) -> list:
    result = _fetch_save(f"{BASE_URL}{endpoint}", "api")
    if result[0] != "error" and result[3]:
        try:
            return json.loads(result[3])
        except json.JSONDecodeError:
            pass
    return []


def mirror_recalldreams():
    global _stats, _changes
    _stats = {"fetched": 0, "skipped": 0, "failed": 0, "changed": 0, "new": 0}
    _changes = []

    log("=" * 50)
    log("  recalldreams.dev — Full Mirror")
    log(f"  {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")
    log("=" * 50)

    log("--- REST API Root ---")
    _fetch_save(f"{BASE_URL}/wp-json/", "api")
    time.sleep(0.15)

    log("--- WP API Collections ---")
    collections = [
        "/wp-json/wp/v2/posts", "/wp-json/wp/v2/pages",
        "/wp-json/wp/v2/media", "/wp-json/wp/v2/categories",
        "/wp-json/wp/v2/tags", "/wp-json/wp/v2/types",
        "/wp-json/wp/v2/users",
    ]
    for ep in collections:
        _fetch_save(f"{BASE_URL}{ep}?per_page=100", "api")
        time.sleep(0.15)

    log("--- Individual Items ---")
    post_ids = []
    page_ids = []
    media_ids = []
    post_links = []
    page_links = []

    for item in _fetch_json("/wp-json/wp/v2/posts?per_page=100"):
        if isinstance(item, dict):
            iid = item.get("id")
            if iid:
                post_ids.append(iid)
                _fetch_save(f"{BASE_URL}/wp-json/wp/v2/posts/{iid}", "api")
                time.sleep(0.1)
                link = item.get("link", "")
                if link:
                    post_links.append(link)

    for item in _fetch_json("/wp-json/wp/v2/pages?per_page=100"):
        if isinstance(item, dict):
            iid = item.get("id")
            if iid:
                page_ids.append(iid)
                _fetch_save(f"{BASE_URL}/wp-json/wp/v2/pages/{iid}", "api")
                time.sleep(0.1)
                link = item.get("link", "")
                if link:
                    page_links.append(link)

    for item in _fetch_json("/wp-json/wp/v2/media?per_page=100"):
        if isinstance(item, dict):
            iid = item.get("id")
            if iid:
                media_ids.append(iid)
                _fetch_save(f"{BASE_URL}/wp-json/wp/v2/media/{iid}", "api")
                time.sleep(0.1)

    log(f"  Posts: {len(post_ids)}  Pages: {len(page_ids)}  Media: {len(media_ids)}")

    log("--- HTML Pages ---")
    all_links = [BASE_URL + "/"] + post_links + page_links
    for link in all_links:
        _fetch_save(link, "html")
        time.sleep(0.25)

    log("--- Media Downloads ---")
    media_urls = set()
    for mid in media_ids:
        result = _fetch_save(f"{BASE_URL}/wp-json/wp/v2/media/{mid}", "api")
        if result[0] != "error" and result[3]:
            try:
                data = json.loads(result[3])
                src = data.get("source_url", "")
                if src:
                    media_urls.add(src)
            except json.JSONDecodeError:
                pass
        time.sleep(0.1)

    for url in sorted(media_urls):
        _fetch_save(url, "media")
        time.sleep(0.2)

    log("--- Discovery Documents ---")
    for path in ["/robots.txt"]:
        _fetch_save(f"{BASE_URL}{path}", "discovery")
        time.sleep(0.1)

    log("--- Extras ---")
    for path in ["/favicon.ico"]:
        _fetch_save(f"{BASE_URL}{path}", "extras")
        time.sleep(0.1)

    log("=" * 50)
    log(f"  COMPLETE — Fetched: {_stats['fetched']}  New: {_stats['new']}  "
        f"Changed: {_stats['changed']}  Skipped: {_stats['skipped']}  "
        f"Failed: {_stats['failed']}")
    log("=" * 50)

    return _stats, _changes


if __name__ == "__main__":
    stats, changes = mirror_recalldreams()
    print(f"Changes: {len(changes)}")
    for c in changes:
        print(f"  {c['detail'][:80]}")
        if c.get('diff'):
            print(f"    diff: {len(c['diff'])} chars")
