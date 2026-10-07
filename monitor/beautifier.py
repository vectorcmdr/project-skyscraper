"""Content beautification for clean diff output.

Beautifies JS blobs, JSON, and HTML inline scripts so diffs show
meaningful line-level changes instead of single-line walls of text.
"""

import json
import re
from pathlib import Path


def beautify(text: str, path_or_ext: str = "") -> str:
    ext = ""
    if path_or_ext:
        if "." in str(path_or_ext):
            ext = Path(str(path_or_ext)).suffix.lower()
        else:
            ext = str(path_or_ext).lower()
            if not ext.startswith("."):
                ext = "." + ext

    if ext == ".json":
        try:
            data = json.loads(text)
            return json.dumps(data, indent=2, ensure_ascii=False) + "\n"
        except (json.JSONDecodeError, ValueError):
            return text

    if ext in (".js", ".mjs"):
        try:
            import jsbeautifier
            return jsbeautifier.beautify(text) + "\n"
        except Exception:
            return text

    if ext in (".html", ".htm"):
        return _beautify_html_scripts(text)

    if ext == ".css":
        return _beautify_css(text)

    return text


def _beautify_html_scripts(html: str) -> str:
    def _replacer(m):
        attrs = (m.group(1) or "").strip()
        content = m.group(2) or ""
        if not content.strip():
            return m.group(0)
        attrs_lower = attrs.lower()
        if re.search(r'\bsrc\s*=', attrs_lower):
            return m.group(0)
        if 'application/json' in attrs_lower or 'application/ld+json' in attrs_lower:
            return m.group(0)
        try:
            import jsbeautifier
            beautified = jsbeautifier.beautify(content)
            tag = f'<script {attrs}>{beautified}</script>' if attrs else f'<script>{beautified}</script>'
            return tag
        except Exception:
            return m.group(0)

    return re.sub(
        r'<script([^>]*?)>([\s\S]*?)</script>',
        _replacer,
        html,
        flags=re.IGNORECASE,
    )


def _beautify_css(css: str) -> str:
    result = re.sub(r'\s*\{', ' {', css)
    result = re.sub(r';(?=\S)', ';\n', result)
    result = re.sub(r'\}', '}\n', result)
    return result
