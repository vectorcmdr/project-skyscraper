"""Diff computation pipeline: beautify -> strip noise -> unified diff.

Produces clean, readable diffs that exclude WordPress auto-generated
metadata and show beautified content for proper line-level comparison.
"""

import difflib

from monitor.beautifier import beautify
from monitor.noise_filter import strip_page_noise, strip_json_noise, diff_has_real_changes
from monitor.config import DIFF_MAX_LINES


def compute_diff(old_bytes: bytes, new_bytes: bytes, url: str,
                 path_hint: str = "", max_lines: int = DIFF_MAX_LINES) -> str | None:
    old_text = old_bytes.decode("utf-8", errors="replace")
    new_text = new_bytes.decode("utf-8", errors="replace")

    if "wp-json" in url or url.endswith(".json"):
        old_text = strip_json_noise(old_text)
        new_text = strip_json_noise(new_text)
    else:
        old_text = strip_page_noise(old_text)
        new_text = strip_page_noise(new_text)
        old_text = beautify(old_text, path_hint)
        new_text = beautify(new_text, path_hint)

    old_lines = old_text.splitlines(keepends=True)
    new_lines = new_text.splitlines(keepends=True)

    diff_iter = difflib.unified_diff(old_lines, new_lines, n=3, lineterm="")
    diff_lines = list(diff_iter)[2:]

    if not diff_lines:
        if old_text != new_text:
            return None
        return None

    if not diff_has_real_changes("\n".join(diff_lines)):
        return None

    if len(diff_lines) > max_lines:
        truncated = diff_lines[:max_lines]
        truncated.append(f"... ({len(diff_lines) - max_lines} more lines)")
        diff_lines = truncated

    return "\n".join(diff_lines)


def compute_text_diff(old_bytes: bytes, new_bytes: bytes) -> str | None:
    import re

    try:
        old_data = __import__("json").loads(old_bytes.decode("utf-8", errors="replace"))
        new_data = __import__("json").loads(new_bytes.decode("utf-8", errors="replace"))
    except Exception:
        return None

    field_pairs = []
    for fname in ("content", "title", "excerpt"):
        old_val = old_data.get(fname, {})
        new_val = new_data.get(fname, {})
        if isinstance(old_val, dict) and "rendered" in old_val:
            old_txt = _strip_html(old_val.get("rendered", ""))
            new_txt = _strip_html(new_val.get("rendered", ""))
        elif isinstance(old_val, str):
            old_txt = _strip_html(old_val)
            new_txt = _strip_html(new_val)
        else:
            continue
        if old_txt != new_txt:
            field_pairs.append((fname, old_txt, new_txt))

    if not field_pairs:
        return None

    parts = []
    for fname, old_txt, new_txt in field_pairs:
        old_lines = old_txt.splitlines()
        new_lines = new_txt.splitlines()
        d = list(difflib.unified_diff(old_lines, new_lines, lineterm="", n=0))[2:]
        if d:
            parts.append(f"--- {fname}")
            parts.extend(d)

    if not parts:
        return None

    result = "\n".join(parts)
    if len(result) > 1000:
        result = result[:997] + "..."
    return result


def _strip_html(text: str) -> str:
    import re
    text = re.sub(r'<[^>]+>', '', text)
    text = __import__("html").unescape(text)
    text = text.replace('\\n', '\n').replace('\\r', '\r').replace('\\t', '\t')
    text = re.sub(r'\\(["\\/])', r'\1', text)
    text = re.sub(r'<[^>\s]+', '', text)
    lines = text.split('\n')
    lines = [re.sub(r'\s+', ' ', l).strip() for l in lines]
    lines = [l for l in lines if l]
    return '\n'.join(lines)


def build_diff_header(url: str, rel_path: str, old_size: int, new_size: int) -> str:
    from datetime import datetime, timezone
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    return (
        f"# Diff: {url}\n"
        f"# File: {rel_path}\n"
        f"# Timestamp: {ts}\n"
        f"# Lines: {old_size} old -> {new_size} new\n"
        "# Beautified: yes\n\n"
    )
