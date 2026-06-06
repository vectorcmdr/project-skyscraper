"""Discord webhook notifications -- concise embeds with minimal diffs."""

import json
import urllib.request
from datetime import datetime, timezone
from collections import defaultdict

from monitor.config import DISCORD_WEBHOOK, DISCORD_PING_ID, USER_AGENT
from monitor.logger import log
from monitor.diff_engine import compute_text_diff
from monitor.url_mapper import url_to_path
from monitor.api_collections import get_user_map

_embed_count = 0


def _reset_embed_count():
    global _embed_count
    _embed_count = 0


def _send_embed(title: str, description: str = "", fields: list = None,
                color: int = 0x00ff88, url: str = None):
    global _embed_count
    if not DISCORD_WEBHOOK:
        return

    _embed_count += 1

    embed = {
        "title": title[:256],
        "description": description[:4096],
        "color": color,
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        "footer": {"text": "Project Skyscraper Monitor"},
    }
    if url:
        embed["url"] = url
    if fields:
        embed["fields"] = [
            {"name": f["name"][:256], "value": f["value"][:1024], "inline": f.get("inline", False)}
            for f in fields[:25]
        ]

    payload = {"embeds": [embed]}

    if _embed_count == 1 and DISCORD_WEBHOOK and DISCORD_PING_ID:
        payload["content"] = f"ATT: IWTS Operator <@{DISCORD_PING_ID}>"

    req = urllib.request.Request(
        DISCORD_WEBHOOK,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "User-Agent": USER_AGENT},
        method="POST",
    )
    try:
        urllib.request.urlopen(req, timeout=10)
        log(f"Discord notification sent: {title[:60]}", "DISCORD")
        return True
    except Exception as e:
        log(f"Discord send failed: {e}", "ERROR")
    return False


_NOTIFY_TYPES = frozenset({
    "sitemap_added", "sitemap_removed", "api_items_added",
    "api_items_removed", "api_items_modified", "page_content_changed",
    "media_replaced", "media_orphan_upload", "media_thumbnail_changed",
    "unpublished_detected",
})


def notify_changes(changes: list, state: dict):
    _reset_embed_count()
    user_map = get_user_map(state)

    by_type = defaultdict(list)
    for c in changes:
        by_type[c["type"]].append(c)

    sitemap_changes = by_type.get("sitemap_added", []) + by_type.get("sitemap_removed", [])
    if sitemap_changes:
        fields = []
        for c in by_type.get("sitemap_added", []):
            fields.append({"name": f"Added ({c['count']})", "value": "\n".join(c["urls"][:15])[:1024]})
        for c in by_type.get("sitemap_removed", []):
            fields.append({"name": f"Removed ({c['count']})", "value": "\n".join(c["urls"][:15])[:1024]})
        _send_embed(
            title=f"Sitemap Changed",
            description=f"+{sum(c['count'] for c in by_type.get('sitemap_added', []))} "
                        f"-{sum(c['count'] for c in by_type.get('sitemap_removed', []))}",
            fields=fields[:10], color=0x00ff88,
        )

    if "api_items_added" in by_type:
        clist = by_type["api_items_added"]
        total = sum(c["count"] for c in clist)
        fields = []
        for c in clist:
            items_str = "\n".join(
                f"#{i['id']}: {_resolve_author(user_map, i.get('author', 0))}: {i.get('title', '(untitled)')}"
                for i in c["items"][:10]
            )
            if items_str:
                fields.append({"name": c["endpoint"].split("/")[-1], "value": items_str[:1024]})
        _send_embed(title=f"New Items: {total}", description="", fields=fields[:10], color=0x00aaff)

    if "api_items_removed" in by_type:
        clist = by_type["api_items_removed"]
        total = sum(c["count"] for c in clist)
        fields = []
        for c in clist:
            ids_str = ", ".join(str(i) for i in c["ids"][:20])
            if ids_str:
                fields.append({"name": c["endpoint"].split("/")[-1], "value": ids_str[:1024]})
        _send_embed(title=f"Removed Items: {total}", description="", fields=fields[:10], color=0xff4444)

    if "api_items_modified" in by_type:
        clist = by_type["api_items_modified"]
        total = sum(c["count"] for c in clist)
        fields = []
        for c in clist:
            ep_label = c["endpoint"].split("/")[-1]
            for item in c.get("items", [])[:5]:
                author = _resolve_author(user_map, item.get("author", 0))
                diff_text = _get_diff_preview(c, item)
                val = f"by {author}\n" if author else ""
                val += diff_text[:900] if diff_text else "(no diff available)"
                fields.append({
                    "name": f"{item.get('title', '(untitled)')} ({ep_label})",
                    "value": val[:1024],
                })
        _send_embed(title=f"Modified Items: {total}", description="", fields=fields[:10], color=0xffaa00)

    if "page_content_changed" in by_type:
        clist = by_type["page_content_changed"]
        fields = []
        for c in clist[:5]:
            page_label = c["url"].split("/")[-1] or c["url"]
            author = _resolve_author(user_map, c.get("author", 0))
            preview = _get_diff_preview(c)
            val = page_label[:200]
            if author:
                val += f" (by {author})"
            if preview:
                val += f"\n{preview[:900]}"
            fields.append({"name": "Page Changed", "value": val[:1024]})
        _send_embed(title=f"Page Content Changed: {len(clist)} page(s)", description="", fields=fields[:10], color=0xff8800)

    if "media_replaced" in by_type:
        clist = by_type["media_replaced"]
        fields = []
        for c in clist[:5]:
            fields.append({
                "name": f"Media #{c['id']}",
                "value": f"Old: {c['old_url'][:200]}\nNew: {c['new_url'][:200]}",
            })
        _send_embed(title=f"Media Replaced: {len(clist)}", description="", fields=fields[:10], color=0xff00ff)

    if "media_thumbnail_changed" in by_type:
        clist = by_type["media_thumbnail_changed"]
        _send_embed(title=f"Thumbnails Changed: {len(clist)}", description="",
                    fields=[{"name": "Details", "value": "\n".join(
                        f"#{c['id']} {c['size']}" for c in clist[:10]
                    )[:1024]}], color=0x88aaff)

    if "media_orphan_upload" in by_type:
        clist = by_type["media_orphan_upload"]
        _send_embed(title=f"Orphan Media: {len(clist)}", description="",
                    fields=[{"name": "Files", "value": "\n".join(
                        f"#{c['id']}: {c.get('title', '(untitled)')}" for c in clist[:10]
                    )[:1024]}], color=0xff00aa)

    if "unpublished_detected" in by_type:
        clist = by_type["unpublished_detected"]
        _send_embed(title=f"Unpublished Content: {len(clist)}", description="",
                    fields=[{"name": "Items", "value": "\n".join(
                        f"#{c['id']} ({c['endpoint']}) HTTP {c['status']}" for c in clist[:10]
                    )[:1024]}], color=0xaa44ff)


def _resolve_author(user_map: dict, author_id) -> str:
    if not author_id:
        return ""
    return user_map.get(author_id, "")


def _get_diff_preview(change: dict, item: dict = None) -> str:
    import html as html_mod
    import re

    diffs = change.get("diffs", [])
    if diffs:
        lines_out = []
        for d in diffs:
            text_diff = d.get("text_diff")
            if text_diff:
                for line in text_diff.split("\n"):
                    if line.startswith("--- "):
                        continue
                    if line and line[0] in ("-", "+"):
                        rest = line[1:].strip()
                        if rest:
                            lines_out.append(f"{line[0]} {rest}")
                continue
            for line in d["diff"].split("\n"):
                line = line.rstrip("\r")
                if not line or line[0] == "@":
                    continue
                prefix = line[0]
                rest = line[1:].strip()
                clean = re.sub(r'<[^>]+>', '', rest)
                if not clean:
                    continue
                lines_out.append(f"{prefix} {clean}")
        result = "\n".join(lines_out)
        if len(result) > 1000:
            result = result[:997] + "..."
        return result

    link = change.get("url", "") or (item.get("link", "") if item else "")
    if not link:
        return "(no content)"

    html_path = url_to_path(link, subdir="html")
    if not html_path.is_file():
        endpoint = change.get("endpoint", "")
        item_id = ""
        if item:
            item_id = str(item.get("id", ""))
        elif change.get("items"):
            item_id = str(change["items"][0].get("id", ""))
        if endpoint and item_id:
            api_path = url_to_path(f"https://project-skyscraper.com{endpoint}/{item_id}", subdir="api")
            if api_path.is_file():
                try:
                    data = json.loads(api_path.read_text(encoding="utf-8"))
                    raw = data.get("content", {}).get("rendered", "") or data.get("excerpt", {}).get("rendered", "") or ""
                    text = re.sub(r'<[^>]+>', '', raw)
                    text = html_mod.unescape(text)
                    if len(text) > 500:
                        text = text[:497] + "..."
                    return text
                except Exception:
                    pass
        return "(no cached content)"

    try:
        raw = html_path.read_text(encoding="utf-8")
    except Exception:
        return "(read error)"

    text = re.sub(r'<script[^>]*>.*?</script>', '', raw, flags=re.DOTALL)
    text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.DOTALL)
    text = re.sub(r'<[^>]+>', '', text)
    text = html_mod.unescape(text)
    lines = [re.sub(r'\s+', ' ', l).strip() for l in text.split('\n')]
    lines = [l for l in lines if l]
    result = "\n".join(lines)
    if len(result) > 500:
        result = result[:497] + "..."
    return result


def notify_trace_change(state: str, last_seen_at: str):
    if not DISCORD_WEBHOOK:
        return

    if state == "ACTIVE":
        color = 0x00ff88
        title = "TRACE: The Architect is ACTIVE"
        desc = f"Last seen: {last_seen_at}"
    else:
        color = 0xff4444
        title = "TRACE: The Architect is LOST"
        desc = f"Last seen: {last_seen_at}"

    _send_embed(title=title, description=desc, color=color)
