"""Page content checking -- hash comparison with noise-aware diffs."""

import re
from datetime import datetime, timezone

from monitor.config import BASE_URL, DATA_DIR
from monitor.http_client import fetch
from monitor.logger import log
from monitor.noise_filter import is_noise_only_page_change
from monitor.diff_engine import compute_diff
from monitor.url_mapper import url_to_path
from monitor.api_collections import find_author_for_url

_NEURAL_URL = f"{BASE_URL}/neural-network-status/"


def check_page_content(url: str, state: dict) -> list:
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

    if url == _NEURAL_URL:
        _extract_connection_count(result.text)

    new_hash = result.hash
    old_hash = page_state.get("hash")

    if old_hash is not None and old_hash != new_hash:
        old_path = url_to_path(url, subdir="html")
        noise_only = False
        if old_path.is_file():
            old_text = old_path.read_text(encoding="utf-8", errors="replace")
            new_text = result.content.decode("utf-8", errors="replace")
            noise_only = is_noise_only_page_change(old_text, new_text)

        if noise_only:
            log(f"Page {url}: hash changed but only noise (suppressed)", "DEEP")
        else:
            diff_text = None
            if old_path.is_file():
                old_bytes = old_path.read_bytes()
                if old_bytes != result.content:
                    diff_text = compute_diff(old_bytes, result.content, url, str(old_path))

            if diff_text is None:
                log(f"Page {url}: hash changed but beautified diff is noise-only (suppressed)", "DEEP")
            else:
                change_obj = {
                    "type": "page_content_changed",
                    "url": url,
                    "old_hash": old_hash,
                    "new_hash": new_hash,
                    "detail": f"Content changed: {url}",
                    "diffs": [{"url": url, "diff": diff_text}],
                }
                author = find_author_for_url(state, url)
                if author:
                    change_obj["author"] = author
                changes.append(change_obj)
                log(f"Page content CHANGED: {url}", "DEEP")
        _save_mirror_copy(url, result)
    elif old_hash is None:
        log(f"Page content first tracked: {url}", "DEEP")
        _save_mirror_copy(url, result)

    page_state["etag"] = result.etag
    page_state["last_modified"] = result.last_modified
    page_state["hash"] = new_hash
    page_state["last_checked"] = datetime.now(timezone.utc).isoformat()

    return changes


def _save_mirror_copy(url: str, result):
    path = url_to_path(url, subdir="html")
    path.parent.mkdir(parents=True, exist_ok=True)
    if result.content:
        path.write_bytes(result.content)


def _extract_connection_count(html: str):
    m = re.search(r'<strong>(\d+)</strong>\s*Live Connection', html)
    if m:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        import json
        (DATA_DIR / "connections.json").write_text(
            json.dumps({"count": int(m.group(1))}), encoding="utf-8"
        )


def get_page_check_batch(state: dict, chunk_size: int = 15) -> list:
    sitemap_urls = list(state.get("sitemap", {}).get("urls", {}).keys())
    if not sitemap_urls:
        return []
    offset = state.setdefault("sitemap", {}).setdefault("_page_check_offset", 0)
    batch = sitemap_urls[offset:offset + chunk_size]
    if len(batch) < chunk_size and len(sitemap_urls) > chunk_size:
        batch.extend(sitemap_urls[:chunk_size - len(batch)])
    state["sitemap"]["_page_check_offset"] = (offset + chunk_size) % len(sitemap_urls)
    return batch
