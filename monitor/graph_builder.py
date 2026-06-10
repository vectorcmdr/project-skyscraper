"""Graph builder -- produces docs/data/graph.json for the neural-net canvas."""

import json
import re
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path

from monitor.config import MIRROR_DIR, DATA_DIR, BASE_URL, IGNORE_HOSTS as _CONFIG_IGNORE_HOSTS
from monitor.logger import log
from monitor.url_mapper import url_to_path
from monitor.api_collections import get_user_map

_SKIP_HOSTS = _CONFIG_IGNORE_HOSTS | {
    "secure.gravatar.com", "0.gravatar.com", "1.gravatar.com", "2.gravatar.com",
    "widgets.wp.com", "jetpack.wordpress.com", "public-api.wordpress.com", "s.w.org",
    "stats.wp.com", "pixel.wp.com",
}
_SKIP_PATH_PREFIXES = (
    "/wp-json/", "/wp-content/", "/wp-includes/", "/xmlrpc.php", "/_static/",
    "/oembed/",
)


def _norm_path(path: str) -> str:
    return path.rstrip("/") or "/"


def build_graph(state: dict) -> dict:
    nodes = []
    links = []

    sitemap_urls = state.get("sitemap", {}).get("urls", {})
    api_data = state.get("api", {})
    user_map = get_user_map(state)

    # Build taxonomy term ID -> path mappings
    cat_id_to_path = {}
    for item in api_data.get("/wp-json/wp/v2/categories", {}).get("items", []):
        iid = item.get("id", 0)
        url = item.get("link", "")
        if iid and url and url.startswith(BASE_URL):
            parsed = urllib.parse.urlparse(url)
            cat_id_to_path[iid] = _norm_path(parsed.path) if parsed.path else "/"

    tag_id_to_path = {}
    for item in api_data.get("/wp-json/wp/v2/tags", {}).get("items", []):
        iid = item.get("id", 0)
        url = item.get("link", "")
        if iid and url and url.startswith(BASE_URL):
            parsed = urllib.parse.urlparse(url)
            tag_id_to_path[iid] = _norm_path(parsed.path) if parsed.path else "/"

    author_id_to_path = {}
    for item in api_data.get("/wp-json/wp/v2/users", {}).get("items", []):
        iid = item.get("id", 0)
        url = item.get("link", "")
        if iid and url and url.startswith(BASE_URL):
            parsed = urllib.parse.urlparse(url)
            author_id_to_path[iid] = _norm_path(parsed.path) if parsed.path else "/"

    # Build index of known URL paths from API items
    known_pages = {}
    id_to_path = {}

    _mirror_item_cache = {}

    def _mirror_items(endpoint: str) -> list:
        if endpoint not in _mirror_item_cache:
            mirror_path = MIRROR_DIR / "api" / "{}.json".format(endpoint.lstrip("/"))
            try:
                _mirror_item_cache[endpoint] = json.loads(mirror_path.read_text(encoding="utf-8"))
            except Exception:
                _mirror_item_cache[endpoint] = []
        return _mirror_item_cache[endpoint]

    _CONTENT_ENDPOINTS = frozenset({
        "/wp-json/wp/v2/navigation", "/wp-json/wp/v2/menu-items",
        "/wp-json/wp/v2/menus", "/wp-json/wp/v2/blocks",
    })

    def _ensure_content_rendered(endpoint: str, item: dict) -> str:
        cr = item.get("content_rendered", "") or ""
        if cr or endpoint not in _CONTENT_ENDPOINTS:
            return cr or ""
        for mi in _mirror_items(endpoint):
            if mi.get("id") == item.get("id"):
                raw = mi.get("content", {})
                if isinstance(raw, dict):
                    return raw.get("rendered", "") or ""
                return ""
        return ""

    def _ensure_taxonomy_count(endpoint: str, item: dict) -> int:
        if "/categories" not in endpoint and "/tags" not in endpoint:
            return -1
        c = item.get("count")
        if c is not None:
            return c
        for mi in _mirror_items(endpoint):
            if mi.get("id") == item.get("id"):
                return mi.get("count", 0) or 0
        return -1

    for endpoint, ep_state in api_data.items():
        items = ep_state.get("items", [])
        if not isinstance(items, list):
            continue
        for item in items:
            url = item.get("link", "")
            if not url or not url.startswith(BASE_URL):
                continue
            parsed = urllib.parse.urlparse(url)
            path = parsed.path if parsed.path else "/"
            item_type = _map_api_type(item.get("type", ""), endpoint)
            title = item.get("title", "")
            if isinstance(title, dict):
                title = title.get("rendered", "")
            author_id = item.get("author", 0) or 0
            item_id = item.get("id", 0)
            norm_path = _norm_path(path)
            if item_id:
                id_to_path[item_id] = norm_path
            raw_cats = item.get("categories", [])
            raw_tags = item.get("tags", [])
            known_pages[norm_path] = {
                "type": item_type,
                "title": title or path.strip("/").split("/")[-1].replace("-", " ").title() or path,
                "author": str(author_id) if author_id else "",
                "date": item.get("date_gmt") or item.get("modified", ""),
                "url": url,
                "author_name": user_map.get(author_id, ""),
                "post_parent": item.get("post_parent", 0) or 0,
                "parent": item.get("parent", 0) or 0,
                "categories": list(raw_cats) if isinstance(raw_cats, list) else [],
                "tags": list(raw_tags) if isinstance(raw_tags, list) else [],
                "count": _ensure_taxonomy_count(endpoint, item),
                "content_rendered": _ensure_content_rendered(endpoint, item),
            }

    # Sitemap hub node
    sitemap_node = {
        "id": "sitemap",
        "type": "sitemap",
        "label": "sitemap.xml",
        "url": f"{BASE_URL}/sitemap.xml",
        "author": "",
        "date": "",
    }
    nodes.append(sitemap_node)

    all_page_paths = {}
    seen_urls = set()

    # Create nodes from sitemap URLs
    for url, meta in sitemap_urls.items():
        parsed = urllib.parse.urlparse(url)
        path = _norm_path(parsed.path) if parsed.path else "/"
        all_page_paths[path] = url
        seen_urls.add(url)

        if path in known_pages:
            info = known_pages[path]
            label = info["title"]
            node_type = info["type"]
            author_val = info.get("author_name") or info.get("author", "")
            date_val = info.get("date", meta.get("lastmod") or "")
        else:
            label = path.strip("/").split("/")[-1].replace("-", " ").title() or "Home"
            node_type = "page"
            author_val = ""
            date_val = meta.get("lastmod") or ""

        nodes.append({
            "id": path,
            "type": node_type,
            "label": label[:80],
            "url": url,
            "author": str(author_val) if author_val else "",
            "date": date_val,
        })
        links.append({"source": "sitemap", "target": path})

    # Add API items not in sitemap as extra nodes
    for path, info in known_pages.items():
        if path not in all_page_paths:
            if info.get("count") == 0:
                continue
            url = info["url"]
            all_page_paths[path] = url
            seen_urls.add(url)
            author_val = info.get("author_name") or info.get("author", "")
            nodes.append({
                "id": path,
                "type": info["type"],
                "label": info["title"][:80],
                "url": url,
                "author": str(author_val) if author_val else "",
                "date": info.get("date", ""),
            })

    # Walk HTML files to extract cross-page links
    external_nodes = {}
    external_count = 0
    MAX_EXTERNAL = 30

    for path, url in all_page_paths.items():
        html_path = url_to_path(url, "html")
        info = known_pages.get(path, {})
        if not html_path.is_file():
            content_html = info.get("content_rendered", "")
            if content_html:
                hrefs = re.findall(r'''(?:href|src)\s*=\s*["\'](.*?)["\']''', content_html, re.IGNORECASE)
            else:
                hrefs = []
        else:
            hrefs = _extract_hrefs(html_path)
        for href in hrefs:
            target_info = _normalize_href(href, url)
            if target_info is None:
                continue

            target_path = target_info["path"]
            target_url = target_info["url"]
            target_host = target_info["host"]
            target_norm = _norm_path(target_path)

            if target_norm == path:
                continue

            if target_norm in all_page_paths:
                links.append({"source": path, "target": target_norm})
                continue

            if target_host in _SKIP_HOSTS:
                continue

            if any(target_path.startswith(p) for p in _SKIP_PATH_PREFIXES):
                continue

            if target_url.startswith("https://wp.me"):
                continue

            if target_url not in seen_urls and target_url.startswith("http"):
                seen_urls.add(target_url)
                if target_url.startswith(BASE_URL) or target_host.endswith("project-skyscraper.com"):
                    ext_id = target_norm
                    ext_label = target_norm.strip("/").split("/")[-1].replace("-", " ").title() or "unknown"
                    if ext_label in ("Wp Json", "Wp Content", "Oembed", "Xmlrpc Php"):
                        continue
                else:
                    if external_count >= MAX_EXTERNAL:
                        continue
                    ext_id = target_url
                    ext_label = target_host.replace("www.", "")
                    external_count += 1

                if ext_id not in {n["id"] for n in nodes}:
                    external_nodes[ext_id] = {
                        "id": ext_id,
                        "type": "external",
                        "label": ext_label[:60],
                        "url": target_url,
                        "author": "",
                        "date": "",
                    }
                    nodes.append(external_nodes[ext_id])

                links.append({"source": path, "target": ext_id})

    # Add API relationship links (media <-> parent post, child page <-> parent page)
    node_ids = {n["id"] for n in nodes}
    for path, info in known_pages.items():
        if path not in node_ids:
            continue
        for rel_field in ("post_parent", "parent"):
            rel_id = info.get(rel_field, 0) or 0
            if rel_id and rel_id in id_to_path:
                parent_path = id_to_path[rel_id]
                if parent_path in node_ids and parent_path != path:
                    links.append({"source": path, "target": parent_path})

    # Taxonomy links: posts -> author, categories, tags
    for path, info in known_pages.items():
        if path not in node_ids:
            continue
        aid = info.get("author", "") or ""
        if aid and aid.isdigit():
            apath = author_id_to_path.get(int(aid))
            if apath and apath in node_ids and apath != path:
                links.append({"source": path, "target": apath})
        for cid in info.get("categories", []):
            cpath = cat_id_to_path.get(cid)
            if cpath and cpath in node_ids and cpath != path:
                links.append({"source": path, "target": cpath})
        for tid in info.get("tags", []):
            tpath = tag_id_to_path.get(tid)
            if tpath and tpath in node_ids and tpath != path:
                links.append({"source": path, "target": tpath})

    # Dataset links: connect event posts to their parent dataset post by sharing the same title
    title_to_posts = {}
    for path, info in known_pages.items():
        if path not in node_ids:
            continue
        title = info.get("title", "")
        if title:
            title_to_posts.setdefault(title, []).append(path)
    for title, paths in title_to_posts.items():
        if len(paths) < 2:
            continue
        # Pick the main post as the one WITHOUT an event date in its path
        main = None
        events = []
        for p in paths:
            if re.search(r'/event-\d{8}', p):
                events.append(p)
            elif main is None:
                main = p
        if main and events:
            for ep in events:
                if ep != main:
                    links.append({"source": ep, "target": main})

    # URL path hierarchy fallback: if node A's path is a subdirectory of
    # node B's, link them.  Only applied to nodes with zero connections
    # so far.
    connected = set()
    for l in links:
        connected.add(l["source"])
        connected.add(l["target"])
    sorted_paths = sorted(node_ids, key=lambda p: p.count("/"), reverse=True)
    for path in sorted_paths:
        if path in connected or path == "/":
            continue
        segments = path.strip("/").split("/")
        for i in range(len(segments) - 1, 0, -1):
            candidate = "/" + "/".join(segments[:i])
            if candidate in node_ids and candidate != "/":
                links.append({"source": path, "target": candidate})
                break

    # Deduplicate links
    seen_links = set()
    deduped_links = []
    for link in links:
        pair = (link["source"], link["target"])
        if pair not in seen_links:
            seen_links.add(pair)
            deduped_links.append(link)

    # Sort: sitemap first, then by type, then label
    type_order = {"sitemap": 0, "page": 1, "post": 2, "media": 3, "external": 4}
    nodes.sort(key=lambda n: (type_order.get(n["type"], 9), n["label"].lower()))

    graph = {
        "updated": datetime.now(timezone.utc).isoformat(),
        "nodes": nodes,
        "links": deduped_links,
    }

    return graph


def _map_api_type(item_type: str, endpoint: str) -> str:
    if item_type == "attachment":
        return "media"
    if item_type == "post":
        return "post"
    if item_type == "page":
        return "page"
    if "/posts" in endpoint:
        return "post"
    if "/pages" in endpoint:
        return "page"
    if "/media" in endpoint:
        return "media"
    return "page"


def _extract_hrefs(html_path: Path) -> list:
    try:
        text = html_path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return []

    hrefs = re.findall(r'''(?:href|src)\s*=\s*["\'](.*?)["\']''', text, re.IGNORECASE)

    result = []
    for href in hrefs:
        href = href.strip()
        if not href:
            continue
        if any(href.startswith(p) for p in ("mailto:", "javascript:", "tel:", "#", "data:")):
            continue
        result.append(href)
    return result


def _normalize_href(href: str, page_url: str) -> dict | None:
    if not href.startswith("http://") and not href.startswith("https://") and not href.startswith("//"):
        href = urllib.parse.urljoin(page_url, href)

    if href.startswith("//"):
        href = "https:" + href

    try:
        parsed = urllib.parse.urlparse(href)
    except Exception:
        return None

    if not parsed.netloc:
        return None

    path = parsed.path if parsed.path else "/"

    return {
        "path": path,
        "url": href,
        "host": parsed.netloc,
    }


def rebuild_on_change(changes: list, state: dict, force: bool = False) -> bool:
    if force:
        graph = build_graph(state)
        write_graph(graph)
        return True

    trigger_types = {
        "sitemap_added", "sitemap_removed",
        "api_items_added", "api_items_removed",
        "page_content_changed",
    }

    for c in changes:
        if c.get("type") in trigger_types:
            graph = build_graph(state)
            write_graph(graph)
            return True

    return False


def write_graph(graph: dict):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    path = DATA_DIR / "graph.json"
    path.write_text(json.dumps(graph, indent=2, ensure_ascii=False), encoding="utf-8")
    log(f"Graph written: {len(graph['nodes'])} nodes, {len(graph['links'])} links", "FILE")
