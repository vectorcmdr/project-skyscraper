"""Noise pattern filtering for WordPress/batcache/cache generated content.

Strips auto-generated, constantly-changing metadata so diffs only
show meaningful content changes.
"""

import hashlib
import json
import re

_PAGE_NOISE_PATTERNS = [
    (re.compile(r'<!--[^>]*?(?:generated|batcached|expires).*?-->', re.DOTALL), ''),
    (re.compile(r'var WP_Statistics_Tracker_Object\s*=\s*\{.*?\}\s*;', re.DOTALL), ''),
    (re.compile(r'<strong>\d+</strong>\s*Live Connection'), '<strong>0</strong> Live Connection'),
    (re.compile(r'[?&]m=\d+'), ''),
    (re.compile(r'e-\d{6}\.js'), 'e-000000.js'),
    (re.compile(r'nonce=[a-f0-9]+'), 'nonce=REMOVED'),
    (re.compile(r'"nonce"\s*:\s*"[a-f0-9]+"'), '"nonce":"REMOVED"'),
    (re.compile(r'generated in \d+\.\d+ seconds'), ''),
    (re.compile(r'\d+ bytes batcached for \d+ seconds'), ''),
    (re.compile(r'served from batcache in \d+\.\d+ seconds'), ''),
    (re.compile(r'expires in \d+ seconds'), ''),
    (re.compile(r'generated \d+ seconds? ago'), ''),
    (re.compile(r'"j"\s*:\s*"\d+:\d+\.\d+-[a-z]\.\d+"'), '"j": "0:0.0-a.0"'),
    (re.compile(r'_stq\.push\([^)]*\)'), ''),
    (re.compile(r'\(new Image\(\)\).src = .+'), ''),
    (re.compile(r'/\*# sourceURL=.+\.(?:min\.)?css\s*\*/'), ''),
    (re.compile(r'\.wp-block-\w+\{[^}]+\}'), ''),
    (re.compile(r'\.wp-container-\w+\{[^}]+\}'), ''),
    (re.compile(r':root\s+:where\([^)]+\)'), ''),
    (re.compile(r'"signature":"[a-f0-9]+"'), '"signature":"REMOVED"'),
    (re.compile(r'"_wpnonce":"[a-f0-9]+"'), '"_wpnonce":"REMOVED"'),
    (re.compile(r'wp-custom-css-[a-f0-9]+'), 'wp-custom-css-XXXXXXXXX'),
    (re.compile(r'<button\b[^>]*type=[\x22\x27]submit[\x22\x27][^>]*>[\s\S]*?</button>', re.IGNORECASE), ''),
    (re.compile(r'"is_logged_in"\s*:\s*"\w*"'), '"is_logged_in":""'),
    (re.compile(r'"ajaxurl"\s*:\s*"[^"]*"'), '"ajaxurl":""'),
    (re.compile(r'"lang"\s*:\s*"[^"]*"'), '"lang":""'),
    (re.compile(r'"display_exif"\s*:\s*"[^"]*"'), '"display_exif":""'),
    (re.compile(r'"display_comments"\s*:\s*"[^"]*"'), '"display_comments":""'),
    (re.compile(r'"single_image_gallery"\s*:\s*"[^"]*"'), '"single_image_gallery":""'),
    (re.compile(r'"jetpack_subscriptions_widget"[^}]*\}'), ''),
    (re.compile(r'img#wpstats\{display:none\}'), ''),
    (re.compile(r'img:is\([^)]*\)\{contain-intrinsic-size:\d+px \d+px\}'), ''),
    (re.compile(r'<script[^>]*type=[\x22\x27]importmap[\x22\x27][^>]*>[\s\S]*?</script>', re.IGNORECASE), ''),
]

_DIFF_NOISE_LINE_PATTERNS = [
    re.compile(r'^[ +-]\s*generated in \d+\.\d+ seconds$'),
    re.compile(r'^[ +-]\s*\d+ bytes batcached for \d+ seconds$'),
    re.compile(r'^[ +-]\s*generated \d+ seconds? ago$'),
    re.compile(r'^[ +-]\s*served from batcache in \d+\.\d+ seconds$'),
    re.compile(r'^[ +-]\s*expires in \d+ seconds$'),
    re.compile(r'^[ +-]\s*<!--$'),
    re.compile(r'^[ +-]\s*-->$'),
    re.compile(r'^[ +-]\s*//# sourceURL=.+$'),
    re.compile(r'^[ +-]\s*<button\s+type.*$'),
    re.compile(r'^[ +-]\s*"nonce"\s*:.*$'),
    re.compile(r'^[ +-]\s*"is_logged_in"\s*:.*$'),
    re.compile(r'^[ +-]\s*"ajaxurl"\s*:.*$'),
    re.compile(r'^[ +-]\s*"lang"\s*:.*$'),
    re.compile(r'^[ +-]\s*"display_exif"\s*:.*$'),
    re.compile(r'^[ +-]\s*"display_comments"\s*:.*$'),
    re.compile(r'^[ +-]\s*"single_image_gallery"\s*:.*$'),
    re.compile(r'^[ +-]\s*_stq\s*='),
    re.compile(r'^[ +-]\s*_stq\.'),
    re.compile(r'^[ +-]\s*\(new Image\(\)\)\.src ='),
    re.compile(r'^[ +-]\s*"j"\s*:\s*"\d+:\d+'),
    re.compile(r'^[ +-]\s*"hp":'),
    re.compile(r'^[ +-]\s*"ac":'),
    re.compile(r'^[ +-]\s*"amp":'),
    re.compile(r'^[ +-]\s*/?\*?# sourceURL=.+$'),
    re.compile(r'^[ +-]\s*\.wp-block-\w+'),
    re.compile(r'^[ +-]\s*\.wp-container-\w+'),
    re.compile(r'^[ +-]\s*:root\s+:where\('),
    re.compile(r'^[ +-]\s*:where\(\.wp-block-'),
    re.compile(r'^[ +-]\s*}[\]\)]*;\s*$'),
    re.compile(r'^[ +-]\s*var Jetpack_Block_Assets_Base_Url'),
    re.compile(r'^[ +-]\s*\{?"baseUrl":'),
    re.compile(r'^[ +-]\s*"concatemoji":'),
    re.compile(r'^[ +-]\s*:root\{--wp--preset--'),
    re.compile(r'^\.\.\.\s*\(truncated\)$'),
    re.compile(r'^[ +-]\s*img#wpstats'),
    re.compile(r'^[ +-]\s*img:is\(.*$'),
    re.compile(r'^[ +-]\s*"@wordpress/interactivity".*$'),
    re.compile(r'^[ +-]\s*"imports".*$'),
    re.compile(r'^[ +-]\s*@wordpress/interactivity.*ver=.*$'),
    re.compile(r'^[ +-]\s*_stq\s*=\s*window[.]?_stq.*$'),
    re.compile(r'^[ +-]\s*_stq\s*=.*\[\]'),
    re.compile(r'^[ .]+\.\.\s*\(\d+ more lines?\)$'),
    re.compile(r'^[ .]+\.\.\s*\(truncated\)$'),
    re.compile(r'^[ +-]\s*.+&#8211;\s*project-skyscraper'),
    re.compile(r'^[ +-]\s*.+\u2013\s*project-skyscraper'),
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
