"""Endpoint discovery -- REST API namespace/route enumeration + mirror fetch helpers."""

import json
from pathlib import Path

from monitor.config import BASE_URL, MIRROR_DIR
from monitor.http_client import fetch, jitter
from monitor.url_mapper import url_to_path
from monitor.logger import log


def discover_sitemap_urls() -> dict:
    urls = {}
    sitemap_index = f"{BASE_URL}/sitemap.xml"
    r = _fetch_and_save(sitemap_index, "discovery")
    if r[0] != "error" and r[3] is not None:
        try:
            import xml.etree.ElementTree as ET
            tree = ET.parse(str(r[1]))
            root = tree.getroot()
            ns = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
            subs = [e.text for e in root.findall(".//sm:sitemap/sm:loc", ns) if e.text]
            for sub in subs:
                _fetch_and_save(sub, "discovery")
                sr = _fetch_and_save(sub, "discovery")
                if sr[0] != "error" and sr[3] is not None:
                    _parse_sub_sitemap(sr[1], urls, ns)
        except Exception:
            pass

    if not urls:
        for alt in ["/sitemap-1.xml", "/image-sitemap-1.xml", "/news-sitemap.xml"]:
            sr = _fetch_and_save(f"{BASE_URL}{alt}", "discovery")
            if sr[0] != "error" and sr[3] is not None:
                try:
                    import xml.etree.ElementTree as ET
                    ns = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
                    st = ET.parse(str(sr[1]))
                    sroot = st.getroot()
                    for e in sroot.findall(".//sm:url/sm:loc", ns):
                        if e.text:
                            urls[e.text] = "page"
                except Exception:
                    pass

    return urls


def _parse_sub_sitemap(path: Path, urls: dict, ns: dict):
    import xml.etree.ElementTree as ET
    try:
        st = ET.parse(str(path))
        sroot = st.getroot()
        for e in sroot.findall(".//sm:url/sm:loc", ns):
            if e.text:
                urls[e.text] = "page"
    except ET.ParseError:
        pass


def fetch_and_save(url: str, subdir: str = "") -> bool:
    path = url_to_path(url, subdir=subdir)
    path.parent.mkdir(parents=True, exist_ok=True)

    old_bytes = path.read_bytes() if path.is_file() else None

    result = fetch(url)
    if result.failed:
        log(f"  FETCH FAIL: {url} -> {result.status}", "ERROR")
        return False
    if result.content is None:
        return False

    if old_bytes is not None and old_bytes == result.content:
        return True

    path.write_bytes(result.content)
    log(f"  FETCH OK: {url} -> {path.relative_to(MIRROR_DIR)}", "FETCH")
    return True


def _fetch_and_save(url: str, subdir: str = "") -> tuple:
    """Fetch URL, save to mirror, return (status, path, code, content)."""
    import hashlib

    path = url_to_path(url, subdir=subdir)
    path.parent.mkdir(parents=True, exist_ok=True)

    req_headers = {
        "User-Agent": "Mozilla/5.0 (project-skyscraper-mirror/1.0)",
        "Accept": "*/*",
    }
    import urllib.request
    import urllib.error

    req = urllib.request.Request(url, headers=req_headers)
    try:
        resp = urllib.request.urlopen(req, timeout=30)
        content = resp.read()
        code = resp.status
    except urllib.error.HTTPError as e:
        content = e.read()
        path.write_bytes(content)
        return ("error", path, e.code, content)
    except Exception:
        return ("error", path, 0, b"")

    old_bytes = path.read_bytes() if path.is_file() else None
    if old_bytes and old_bytes == content:
        return ("skipped", path, code, content)

    path.write_bytes(content)
    return ("ok", path, code, content)


def discover_rest_api() -> tuple:
    result = fetch(f"{BASE_URL}/wp-json/")
    namespaces = []
    route_keys = []
    if result.ok:
        try:
            data = json.loads(result.text)
            ns_list = data.get("namespaces", [])
            routes = data.get("routes", {})
            for ns in ns_list:
                namespaces.append(f"/wp-json/{ns}")
            route_keys = list(routes.keys()) if isinstance(routes, dict) else []
        except json.JSONDecodeError:
            pass
    return namespaces, route_keys


KNOWN_NS_ROOTS = [
    "/wp-json/jetpack/v4", "/wp-json/wpcom/v2", "/wp-json/wpcom/v3",
    "/wp-json/wpcomsh/v1", "/wp-json/code-snippets/v1",
    "/wp-json/crowdsignal-forms/v1", "/wp-json/wp-statistics/v2",
    "/wp-json/wp-site-health/v1", "/wp-json/wp-abilities/v1",
    "/wp-json/akismet/v1", "/wp-json/my-jetpack/v1",
    "/wp-json/jetpack-boost/v1", "/wp-json/jetpack-global-styles/v1",
    "/wp-json/newspack-blocks/v1", "/wp-json/videopress/v1",
    "/wp-json/help-center", "/wp-json/wp-block-editor/v1",
    "/wp-json/wp-sync/v1",
]

JETPACK_SUBS = [
    "/wp-json/jetpack/v4/site", "/wp-json/jetpack/v4/module",
    "/wp-json/jetpack/v4/module/all", "/wp-json/jetpack/v4/module/protect",
    "/wp-json/jetpack/v4/scan", "/wp-json/jetpack/v4/scan/history",
    "/wp-json/jetpack/v4/sync/status", "/wp-json/jetpack/v4/sync/checksum",
    "/wp-json/jetpack/v4/connection", "/wp-json/jetpack/v4/connection/url",
    "/wp-json/jetpack/v4/identity-crisis", "/wp-json/jetpack/v4/plugins",
    "/wp-json/jetpack/v4/update-plugins", "/wp-json/jetpack/v4/recommendations/data",
    "/wp-json/jetpack/v4/recommendations/site-pages",
    "/wp-json/jetpack/v4/backup", "/wp-json/jetpack/v4/backup-ux",
    "/wp-json/jetpack/v4/backup-ux/data", "/wp-json/jetpack/v4/stats-app",
    "/wp-json/jetpack/v4/import", "/wp-json/jetpack/v4/explat",
    "/wp-json/jetpack/v4/blaze-app", "/wp-json/jetpack/v4/blaze",
    "/wp-json/jetpack/v4/videopress", "/wp-json/jetpack/v4/social",
    "/wp-json/jetpack/v4/search", "/wp-json/jetpack/v4/search/plan",
    "/wp-json/jetpack/v4/search/settings", "/wp-json/jetpack/v4/search/stats",
]
