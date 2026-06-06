"""Noise pattern filtering for WordPress/batcache/cache generated content.

Strips auto-generated, constantly-changing metadata so diffs only
show meaningful content changes.
"""

import hashlib
import json
import re

_PAGE_NOISE_PATTERNS = [
    (re.compile(r'<!--[^>]*?(?:generated in|batcached).*?-->', re.DOTALL), ''),
    (re.compile(r'var WP_Statistics_Tracker_Object\s*=\s*\{.*?\}\s*;', re.DOTALL), ''),
    (re.compile(r'<strong>\d+</strong>\s*Live Connection'), '<strong>0</strong> Live Connection'),
    (re.compile(r'[?&]m=\d+'), ''),
    (re.compile(r'e-\d{6}\.js'), 'e-000000.js'),
    (re.compile(r'nonce=[a-f0-9]+'), 'nonce=REMOVED'),
    (re.compile(r'generated in \d+\.\d+ seconds'), ''),
    (re.compile(r'\d+ bytes batcached for \d+ seconds'), ''),
    (re.compile(r'served from batcache in \d+\.\d+ seconds'), ''),
    (re.compile(r'expires in \d+ seconds'), ''),
    (re.compile(r'generated \d+ seconds? ago'), ''),
    (re.compile(r'"j":"\d+:\d+\.\d+-[a-z]\.\d+"'), '"j":"0:0.0-a.0"'),
    (re.compile(r'"signature":"[a-f0-9]+"'), '"signature":"REMOVED"'),
    (re.compile(r'"_wpnonce":"[a-f0-9]+"'), '"_wpnonce":"REMOVED"'),
    (re.compile(r'wp-custom-css-[a-f0-9]+'), 'wp-custom-css-XXXXXXXXX'),
]

_DIFF_NOISE_LINE_PATTERNS = [
    re.compile(r'^[ +-]\tgenerated in \d+\.\d+ seconds$'),
    re.compile(r'^[ +-]\t\d+ bytes batcached for \d+ seconds$'),
    re.compile(r'^[ +-]\tgenerated \d+ seconds? ago$'),
    re.compile(r'^[ +-]\tserved from batcache in \d+\.\d+ seconds$'),
    re.compile(r'^[ +-]\texpires in \d+ seconds$'),
    re.compile(r'^[ +-]<!--$'),
    re.compile(r'^[ +-]-->$'),
]

_JSON_NOISE_KEYS = frozenset({
    "_links", "_embedded", "guid", "meta", "code",
    "modified", "modified_gmt", "date_gmt",
    "id", "author", "status", "type", "slug", "template", "featured_media",
    "comment_status", "ping_status", "menu_order", "parent", "order",
    "generated_slug", "_private", "link", "class_list", "categories",
    "tags", "sticky", "format", "password",
})


def strip_page_noise(html: str) -> str:
    for pattern, replacement in _PAGE_NOISE_PATTERNS:
        html = pattern.sub(replacement, html)
    return html


def is_noise_only_page_change(old_text: str, new_text: str) -> bool:
    old_stripped = strip_page_noise(old_text)
    new_stripped = strip_page_noise(new_text)
    return hashlib.md5(old_stripped.encode("utf-8")).hexdigest() == \
           hashlib.md5(new_stripped.encode("utf-8")).hexdigest()


def strip_json_noise(text: str) -> str:
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return text

    def _walk(v):
        if isinstance(v, dict):
            return {k: _walk(v) for k, v in v.items() if k not in _JSON_NOISE_KEYS}
        if isinstance(v, list):
            return [_walk(i) for i in v]
        return v

    cleaned = _walk(data)
    return json.dumps(cleaned, indent=2, sort_keys=False, ensure_ascii=False)


def is_noise_diff_line(line: str) -> bool:
    return any(r.match(line) for r in _DIFF_NOISE_LINE_PATTERNS)


def diff_has_real_changes(diff_text: str) -> bool:
    changed_lines = []
    for line in diff_text.splitlines():
        if line.startswith(('--- ', '+++ ', '@@', '#', 'diff --git')):
            continue
        if line.startswith(('-', '+')):
            changed_lines.append(line)

    if not changed_lines:
        return False

    for line in changed_lines:
        if is_noise_diff_line(line):
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
            for l in changed_lines
        )
        if not paired:
            return True
    return False
