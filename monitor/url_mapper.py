"""URL-to-local-path mapping -- single canonical implementation."""

import re
import urllib.parse
from pathlib import Path

from monitor.config import MIRROR_DIR, BINARY_EXTENSIONS


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


def is_binary_url(url: str) -> bool:
    ext = Path(urllib.parse.urlparse(url).path).suffix.lower()
    return ext in BINARY_EXTENSIONS
