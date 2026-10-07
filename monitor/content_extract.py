"""Visible-content extraction for external site change detection.

Reduces any HTML page to a "content signature" containing only what a
human sees and interacts with: visible text, structural tags, and
semantic attributes (href/src/alt/title/srcset/poster/value/placeholder/
aria-label). Everything else -- head, doctype, scripts, styles, comments,
svg, classes, ids, data-* attributes -- is infrastructure and is discarded
by construction rather than by pattern. Two renders that differ only in
infrastructure (CSS compilations, tokens, nonces, cache variants) produce
identical signatures; any real content change produces a different one.
"""

import difflib
import html as html_mod
import re

_DOCTYPE_RE = re.compile(r"<!DOCTYPE[^>]*>", re.IGNORECASE)
_HEAD_RE = re.compile(r"<head[^>]*>.*?</head>", re.DOTALL | re.IGNORECASE)
_SCRIPT_STYLE_RE = re.compile(
    r"<(script|style|template|noscript)[^>]*>.*?</\1>", re.DOTALL | re.IGNORECASE
)
_SVG_RE = re.compile(r"<svg[^>]*>.*?</svg>", re.DOTALL | re.IGNORECASE)
_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)

_SEMANTIC_ATTRS = (
    "href", "src", "alt", "title", "srcset", "poster",
    "value", "placeholder", "aria-label",
)
_ATTR_RE = re.compile(
    r"\b(href|src|alt|title|srcset|poster|value|placeholder|aria-label)"
    r"\s*=\s*(\"[^\"]*\"|'[^']*'|[^\s>]+)",
    re.IGNORECASE,
)
_HIDDEN_INPUT_RE = re.compile(r"\btype\s*=\s*[\"']?hidden[\"']?", re.IGNORECASE)
# Jetpack block asset preloads (swiper.js etc.) injected into rendered pages
# by plugin updates -- infrastructure, never content. Skipped by name so a
# plugin update adding/removing them never changes the page signature.
_PLUGIN_ASSET_LINK_RE = re.compile(
    r"\bhref\s*=\s*(?:\"[^\"]*\"|'[^']*'|[^\s>]+)",
    re.IGNORECASE,
)
_TAG_RE = re.compile(r"<(/)?([a-zA-Z][a-zA-Z0-9]*)((?:\s+[^<>]*?)?)(/?)>")
_WS_RE = re.compile(r"\s+")


def _clean_text(chunk: str) -> str:
    chunk = html_mod.unescape(chunk)
    chunk = chunk.replace("\xa0", " ")
    return _WS_RE.sub(" ", chunk).strip()


def extract_content_signature(html_text: str) -> str:
    """Return a normalized visible-content signature for an HTML page."""
    if not html_text:
        return ""
    text = _DOCTYPE_RE.sub("", html_text)
    text = _HEAD_RE.sub("", text)
    text = _SCRIPT_STYLE_RE.sub("", text)
    text = _SVG_RE.sub("", text)
    text = _COMMENT_RE.sub("", text)

    out = []
    pos = 0
    for m in _TAG_RE.finditer(text):
        if m.start() > pos:
            chunk = _clean_text(text[pos:m.start()])
            if chunk:
                out.append(chunk)
        closing = m.group(1)
        name = m.group(2)
        attrs = m.group(3) or ""
        self_close = m.group(4)
        if closing:
            out.append(f"</{name}>")
        else:
            if name.lower() == "link" and "/jetpack/_inc/blocks/" in (
                _PLUGIN_ASSET_LINK_RE.search(attrs).group(0).strip("\"'")
                if _PLUGIN_ASSET_LINK_RE.search(attrs) else ""
            ):
                # Jetpack block asset preload (swiper.js etc.) -- plugin
                # update churn, never content. Drop the whole tag so plugin
                # updates adding/removing it don't change the signature.
                pos = m.end()
                continue
            kept = []
            is_hidden_input = (
                name == "input" and bool(_HIDDEN_INPUT_RE.search(attrs))
            )
            for am in _ATTR_RE.finditer(attrs):
                attr_name = am.group(1).lower()
                if attr_name == "value" and is_hidden_input:
                    # Hidden inputs carry infra payloads (Jetpack form
                    # JWTs, nonces) that rotate per render -- not content.
                    continue
                if attr_name == "data-image-meta":
                    # WP attachment EXIF metadata -- machine-generated,
                    # stripped/added by plugin updates, never content.
                    continue
                val = html_mod.unescape(am.group(2).strip("\"'"))
                kept.append(f"{attr_name}={val}")
            attr_str = (" " + " ".join(kept)) if kept else ""
            out.append(f"<{name}{attr_str}{'/' if self_close else ''}>")
        pos = m.end()
    if pos < len(text):
        chunk = _clean_text(text[pos:])
        if chunk:
            out.append(chunk)
    return "\n".join(out)


def compute_content_diff(old_sig: str, new_sig: str, context: int = 2) -> str | None:
    """Unified diff between two content signatures; None when identical."""
    old_lines = old_sig.splitlines()
    new_lines = new_sig.splitlines()
    if old_lines == new_lines:
        return None
    diff_iter = difflib.unified_diff(old_lines, new_lines, n=context, lineterm="")
    return "\n".join(diff_iter)