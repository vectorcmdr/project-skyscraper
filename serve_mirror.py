#!/usr/bin/env python3
"""
serve_mirror.py - Local HTTP server for the project-skyscraper.com mirror.

Rewrites all live URLs -> local paths on-the-fly so the site works fully
offline. No mirror files are modified.

Usage:
    python serve_mirror.py           # Port 8080
    python serve_mirror.py 3000      # Custom port
"""

import http.server
import mimetypes
import os
import re
import sys
import urllib.parse
from datetime import datetime
from pathlib import Path

MIRROR_DIR = Path(__file__).parent.resolve()
DEFAULT_PORT = 8080
BASE_DOMAIN = "project-skyscraper.com"
BASE_URL = f"https://{BASE_DOMAIN}"

BINARY_EXTS = frozenset({
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".svg",
    ".woff2", ".woff", ".ttf", ".eot", ".otf",
    ".zip", ".gz", ".pdf", ".mp4", ".webm", ".mp3",
})

# Subdirectory lookup order for URL routing
SUBDIRS = ["html", "api", "media", "assets", "discovery", "extras", "endpoints", "third_party"]

CDN_HOSTS = frozenset({
    "fonts.wp.com", "s0.wp.com", "stats.wp.com", "c0.wp.com",
    "secure.gravatar.com", "widgets.wp.com", "jetpack.wordpress.com",
    "public-api.wordpress.com", "0.gravatar.com", "1.gravatar.com",
    "2.gravatar.com", "s.w.org", "stats.wordpress.com",
})


# --- URL -> local file resolution ---

def resolve_path(request_path: str) -> Path | None:
    path = urllib.parse.urlparse(request_path).path.rstrip("/") or "/"
    rel = path.lstrip("/")

    # Direct hit under mirror root
    cand = MIRROR_DIR / rel
    if cand.is_file():
        return cand

    # Try each subdirectory with the raw relative path
    for sub in SUBDIRS:
        cand = MIRROR_DIR / sub / rel
        if cand.is_file():
            return cand

    # Extensionless -> try with .html
    if not Path(path).suffix:
        for sub in ("html",):
            cand = MIRROR_DIR / sub / (rel + ".html")
            if cand.is_file():
                return cand

    # API paths -> try with .json (and handle /wp-json/ -> wp-json.json)
    if path.startswith("/wp-json") or path.startswith("/?rest_route"):
        for sub in ("api",):
            for try_rel in (rel, rel.rstrip("/")):
                cand = MIRROR_DIR / sub / (try_rel + ".json")
                if cand.is_file():
                    return cand

    # Nested path flattened with underscores (old url_to_path pattern)
    # /a/b/c/ -> a_b_c.html
    flat = path.strip("/").replace("/", "_") + ".html"
    cand = MIRROR_DIR / "html" / flat
    if cand.is_file():
        return cand

    # Nested path without flattening (current url_to_path pattern)
    # /a/b/c/ -> a/b/c.html
    nested = (path.strip("/") + ".html").replace("//", "/")
    cand = MIRROR_DIR / "html" / nested
    if cand.is_file():
        return cand

    # Try flat name for root
    if path in ("/", ""):
        cand = MIRROR_DIR / "html" / "index.html"
        if cand.is_file():
            return cand

    # For CDN-hosted assets: try to find via walk when query-params
    # are embedded in the filename (e.g. bilmur.min.js_m_202622)
    #   requested: bilmur.min.js  ->  stored: bilmur.min.js_m_202622
    tp_dir = MIRROR_DIR / "third_party"
    if tp_dir.exists():
        last_part = rel.split("/")[-1]
        last_clean = last_part.split("?")[0]
        for tp_root, tp_dirs, tp_files in os.walk(str(tp_dir)):
            for tf in tp_files:
                if tf.startswith(last_clean):
                    return Path(tp_root) / tf

    # Query-string paths: try scanning parent dir for matching files
    # (e.g. /wp-json/oembed/1.0/embed?url=... -> files starting with embed_)
    parsed = urllib.parse.urlparse(request_path)
    if parsed.query:
        for sub in SUBDIRS:
            parent = MIRROR_DIR / sub / "/".join(rel.split("/")[:-1])
            if parent.is_dir():
                last_seg = rel.rsplit("/", 1)[-1]
                for f in sorted(parent.iterdir()):
                    if f.is_file() and f.name.startswith(last_seg):
                        return f

    # .unresolved marker file - for endpoints that can't be fetched
    # (API namespace roots, etc.) but should return a description
    for sub in SUBDIRS:
        for try_rel in (rel, rel.rstrip("/")):
            cand = MIRROR_DIR / sub / (try_rel + ".unresolved")
            if cand.is_file():
                return cand


# --- HTML/CSS/JS URL rewriting ---

def rewrite_text(text: str) -> str:
    # 1) Strip BASE_DOMAIN URLs
    text = text.replace(BASE_URL, "")
    text = text.replace(f"//{BASE_DOMAIN}", "")

    # 2) Strip i0.wp.com (Photon CDN) - these reference project-skyscraper content
    text = text.replace(f"https://i0.wp.com/{BASE_DOMAIN}", "")
    text = text.replace(f"//i0.wp.com/{BASE_DOMAIN}", "")

    # 3) Strip other CDN hosts - they're stored under third_party/
    for host in CDN_HOSTS:
        text = text.replace(f"https://{host}", "")
        text = text.replace(f"//{host}", "")

    # 4) Silence sourceURL annotations (inline CSS style markers)
    text = re.sub(r'/\*# sourceURL=.*?\*/', '/* sourceURL=local */', text)

    # 5) Fix //sourceURL= (JS sourceURL comments in inline scripts)
    text = re.sub(r'//# sourceURL=.*', '//# sourceURL=local', text)

    # 6) Remove dns-prefetch / preconnect to external hosts (not needed locally)
    text = re.sub(
        r'<link[^>]*rel=["\'](dns-prefetch|preconnect)["\'][^>]*/?>',
        '', text
    )

    # 7) Make live visitor counter just show static text
    text = re.sub(
        r'<span class="online-iterations "><strong>\d+</strong> Live Connection Attempts</span>',
        '<span class="online-iterations "><strong>∞</strong> Local Mirror</span>',
        text
    )

    # 8) Disable the 15s polling setInterval that re-fetches window.location
    # Replace the polling interval with a no-op
    text = text.replace(
        "setInterval(() => {",
        "// setInterval(() => { // disabled in local mirror"
    )
    # But we need to also handle the closing of the setInterval call
    text = text.replace(
        "}, 15000);",
        "// }, 15000); // disabled in local mirror"
    )

    return text


# --- HTTP handler ---

class MirrorHandler(http.server.BaseHTTPRequestHandler):
    # Don't log every request by default - too noisy
    quiet = False

    def do_GET(self):
        # Find local file
        local_path = resolve_path(self.path)

        if local_path is None:
            self.send_response(404)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"404 - not found in mirror")
            if not self.quiet:
                print(f"  {self.client_address[0]}  404  {self.path}")
            return

        try:
            raw = local_path.read_bytes()
        except Exception as e:
            self.send_response(500)
            self.end_headers()
            self.wfile.write(f"500 - {e}".encode())
            return

        ext = local_path.suffix.lower()
        mime = mimetypes.guess_type(str(local_path))[0] or "application/octet-stream"

        # .unresolved marker files are always plain text
        if local_path.suffix == ".unresolved":
            mime = "text/plain; charset=utf-8"

        # Files with unrecognized extension: try substring match for
        # known extensions (catches bilmur.min.js_m_202622 -> .js)
        if mime == "application/octet-stream":
            for known_ext, known_mime in [
                (".js", "application/javascript"),
                (".json", "application/json"),
                (".css", "text/css"),
                (".html", "text/html"),
                (".xml", "text/xml"),
            ]:
                if known_ext in local_path.name.lower():
                    mime = known_mime + "; charset=utf-8"
                    break

        # Still unrecognized: sniff content for JSON ({ or [)
        if mime == "application/octet-stream":
            try:
                text_sample = raw[:50].decode("utf-8")
                if text_sample.strip().startswith(("{", "[")):
                    mime = "application/json; charset=utf-8"
            except (UnicodeDecodeError, ValueError):
                pass

        is_text = ext not in BINARY_EXTS or ext == ".svg"

        if is_text:
            try:
                text = raw.decode("utf-8")
                text = rewrite_text(text)
                raw = text.encode("utf-8")
                # Set proper HTML charset
                if ext in (".html", ".htm") or mime == "text/html" or not ext:
                    mime = 'text/html; charset=utf-8'
                elif mime.startswith("text/") or mime == "application/javascript":
                    mime = mime.split(";")[0] + "; charset=utf-8"
            except UnicodeDecodeError:
                pass  # Serve binary as-is

        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(raw)

        if not self.quiet:
            size = len(raw)
            label = "OK"
            if size > 1024 * 1024:
                label = f"OK {size / 1024 / 1024:.1f}MB"
            print(f"  {self.client_address[0]}  200  {label}  {self.path}")

    def log_message(self, format, *args):
        pass  # Suppress default logging, we do our own


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    quiet = "--quiet" in sys.argv or "-q" in sys.argv
    port = int(args[0]) if args else DEFAULT_PORT

    MirrorHandler.quiet = quiet

    server = http.server.HTTPServer(("", port), MirrorHandler)
    print()
    print(f"  project-skyscraper.com - Local Mirror Server")
    print(f"  {MIRROR_DIR}")
    print(f"  Serving at http://localhost:{port}")
    print()
    print(f"  Press Ctrl+C to stop")
    print()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  Shutting down...")
        server.server_close()


if __name__ == "__main__":
    main()
