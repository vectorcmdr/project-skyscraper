"""Discord webhook notifications -- concise embeds with minimal diffs."""

import json
import time
import urllib.request
from datetime import datetime, timezone
from collections import defaultdict

from monitor.config import DISCORD_WEBHOOKS, USER_AGENT
from monitor.logger import log
from monitor.diff_engine import compute_text_diff
from monitor.url_mapper import url_to_path
from monitor.api_collections import get_user_map

MEDIA_PER_ITEM_CAP = 20  # per-cycle cap for individual media notifications


def _send_embed(title: str, description: str = "", fields: list = None,
                color: int = 0x00ff88, url: str = None, image: str = None):
    if not DISCORD_WEBHOOKS:
        return

    embed = {
        "title": title[:256],
        "description": description[:4096],
        "color": color,
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        "footer": {"text": "Project Skyscraper Monitor"},
    }
    if url:
        embed["url"] = url
    if image:
        embed["image"] = {"url": image}
    if fields:
        embed["fields"] = [
            {"name": f["name"][:256], "value": f["value"][:1024], "inline": f.get("inline", False)}
            for f in fields[:25]
        ]

    payload = {"embeds": [embed]}

    sent_any = False
    for idx, webhook_url in enumerate(DISCORD_WEBHOOKS):
        req = urllib.request.Request(
            webhook_url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json", "User-Agent": USER_AGENT},
            method="POST",
        )
        try:
            urllib.request.urlopen(req, timeout=10)
            sent_any = True
            if idx == 0:
                log(f"Discord notification sent: {title[:60]}", "DISCORD")
        except Exception as e:
            log(f"Discord send failed ({webhook_url[:60]}...): {e}", "ERROR")
    return sent_any


_NOTIFY_TYPES = frozenset({
    "sitemap_added", "sitemap_removed", "api_items_added",
    "api_items_removed", "api_items_modified", "page_content_changed",
    "media_replaced", "media_orphan_upload", "media_thumbnail_changed",
    "unpublished_detected", "external_unpublished_to_published",
    "external_youtube_video_public", "external_youtube_video_hidden",
    "external_youtube_video_removed", "external_youtube_count_changed",
})


def notify_changes(changes: list, state: dict):
    user_map = get_user_map(state)

    by_type = defaultdict(list)
    for c in changes:
        by_type[c["type"]].append(c)

    media_sent = 0  # shared per-cycle counter for individual media notifications

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
        non_media_total = sum(c["count"] for c in clist if "/media" not in c["endpoint"])
        fields = []
        media_overflow = []
        for c in clist:
            ep_label = c["endpoint"].split("/")[-1]
            if "/media" in c["endpoint"]:
                for i in c["items"]:
                    if media_sent < MEDIA_PER_ITEM_CAP:
                        media_sent += 1
                        _send_embed(
                            title=f"New Media: {_item_label(i)}",
                            description=(
                                f"by {_resolve_author(user_map, i.get('author', 0))}"
                                if i.get("author") else ""
                            ),
                            url=i.get("link") or None,
                            image=i.get("source_url") or None,
                            color=0x00aaff,
                        )
                        time.sleep(0.25)
                    else:
                        media_overflow.append(
                            f"#{i['id']}: {_resolve_author(user_map, i.get('author', 0))}: {_item_label(i)}"
                        )
            else:
                item_lines = [_format_added_item(i, user_map) for i in c["items"][:10]]
                if item_lines:
                    fields.append({"name": ep_label, "value": "\n".join(item_lines)[:1024]})
        if media_overflow:
            fields.append({"name": "media", "value": "\n".join(media_overflow)[:1024]})
        if fields:
            _send_embed(title=f"New Items: {non_media_total + len(media_overflow)}",
                        description="", fields=fields[:10], color=0x00aaff)

    if "api_items_removed" in by_type:
        clist = by_type["api_items_removed"]
        total = sum(c["count"] for c in clist)
        fields = []
        for c in clist:
            items = c.get("items", [])
            if items:
                items_str = "\n".join(
                    f"{i['id']} ({i.get('link', '?')})"
                    for i in items[:15]
                )
                fields.append({"name": c["endpoint"].split("/")[-1], "value": items_str[:1024]})
            else:
                ids_str = ", ".join(str(i) for i in c["ids"][:20])
                if ids_str:
                    fields.append({"name": c["endpoint"].split("/")[-1], "value": ids_str[:1024]})
        _send_embed(title=f"Removed Items: {total}", description="", fields=fields[:10], color=0xff4444)

    if "api_items_modified" in by_type:
        clist = by_type["api_items_modified"]
        fields = []
        shown = 0
        for c in clist:
            ep_label = c["endpoint"].split("/")[-1]
            for item in c.get("items", [])[:5]:
                author = _resolve_author(user_map, item.get("author", 0))
                diff_text = _get_diff_preview(c, item)
                if not diff_text:
                    continue
                val = f"by {author}\n" if author else ""
                val += diff_text[:900]
                fields.append({
                    "name": f"{_item_label(item)} ({ep_label})",
                    "value": val[:1024],
                })
                shown += 1
        if fields:
            _send_embed(title=f"Modified Items: {shown}", description="", fields=fields[:10], color=0xffaa00)

    if "page_content_changed" in by_type:
        import re as _re
        _mb_re = _re.compile(r'Memory_bloc_restoration:\s*(\d+/\d+)\s*Completed')
        memory_bloc = []
        regular = []
        for c in by_type["page_content_changed"]:
            if c.get("_password_recovered"):
                continue
            if not _get_diff_preview(c):
                continue
            diffs = c.get("diffs", [])
            if diffs and _mb_re.search(diffs[0].get("diff", "")):
                memory_bloc.append(c)
            else:
                regular.append(c)

        if regular:
            fields = []
            for c in regular[:5]:
                page_url = c.get("url", "")
                page_link = f"<{page_url}>" if page_url else "(no url)"
                author = _resolve_author(user_map, c.get("author", 0))
                preview = _get_diff_preview(c)
                val = page_link[:200]
                if author:
                    val += f" (by {author})"
                if preview:
                    val += f"\n{preview[:900]}"
                fields.append({"name": "Page Changed", "value": val[:1024]})
            _send_embed(title=f"Page Content Changed: {len(regular)} page(s)", description="", fields=fields[:10], color=0xff8800)

        if memory_bloc:
            groups = {}
            for c in memory_bloc:
                diffs = c.get("diffs", [])
                if diffs:
                    m = _mb_re.findall(diffs[0].get("diff", ""))
                    if len(m) >= 2:
                        old_val, new_val = m[0], m[1]
                        if old_val == new_val:
                            continue
                        groups.setdefault(new_val, {"new_value": new_val, "old_value": old_val, "changes": []})
                        groups[new_val]["changes"].append(c)

            for val, grp in groups.items():
                page_count = len(grp["changes"])
                changes = grp["changes"]
                samples = "\n".join(
                    f"<{c.get('url', '')}>" if c.get("url") else ""
                    for c in changes[:10]
                )
                _send_embed(
                    title=f"Memory Bloc Restoration: {grp['old_value']} \u2192 {grp['new_value']}",
                    description=f"{page_count} page(s) updated",
                    fields=[{"name": "Pages", "value": samples[:1024]}],
                    color=0xff8800,
                )

    if "media_replaced" in by_type:
        clist = by_type["media_replaced"]
        overflow = []
        for c in clist:
            if media_sent >= MEDIA_PER_ITEM_CAP:
                overflow.append(c)
                continue
            media_sent += 1
            link = c.get("new_url", "")
            old = c.get("old_url", "")
            desc = f"Old: {old[:200]}" if old else ""
            if link:
                desc = f"{desc}\nNew: <{link}>" if desc else f"New: <{link}>"
            _send_embed(
                title=f"Media Replaced: #{c['id']}",
                description=desc,
                url=link or None,
                image=link or None,
                color=0xff00ff,
            )
            time.sleep(0.25)
        if overflow:
            _send_embed(
                title=f"Media Replaced: {len(overflow)} more",
                description="",
                fields=[{
                    "name": "Items",
                    "value": "\n".join(f"#{c['id']}: {c.get('new_url', '')}" for c in overflow)[:1024],
                }],
                color=0xff00ff,
            )

    if "media_thumbnail_changed" in by_type:
        clist = by_type["media_thumbnail_changed"]
        _send_embed(title=f"Thumbnails Changed: {len(clist)}", description="",
                    fields=[{"name": "Details", "value": "\n".join(
                        f"#{c['id']} {c['size']}" for c in clist[:10]
                    )[:1024]}], color=0x88aaff)

    if "media_orphan_upload" in by_type:
        clist = by_type["media_orphan_upload"]
        overflow = []
        for c in clist:
            if media_sent >= MEDIA_PER_ITEM_CAP:
                overflow.append(c)
                continue
            media_sent += 1
            url = c.get("url", "")
            title = c.get("title") or f"Media #{c.get('id', '?')}"
            desc = f"by {_resolve_author(user_map, c.get('author', 0))}" if c.get("author") else ""
            _send_embed(
                title=f"Orphan Media: {title}",
                description=desc,
                url=url or None,
                image=url or None,
                color=0xff00aa,
            )
            time.sleep(0.25)
        if overflow:
            _send_embed(
                title=f"Orphan Media: {len(overflow)} more",
                description="",
                fields=[{
                    "name": "Files",
                    "value": "\n".join(
                        _item_label({"title": c.get('title', '(untitled)'), "link": c.get("url", "")})
                        for c in overflow
                    )[:1024],
                }],
                color=0xff00aa,
            )

    if "unpublished_detected" in by_type:
        clist = by_type["unpublished_detected"]
        from monitor.config import DATA_DIR as _dd
        _feed_path = _dd / "feed.json"
        _known_ids = set()
        if _feed_path.is_file():
            try:
                import json as _json
                _feed = _json.loads(_feed_path.read_text(encoding="utf-8"))
                for _e in _feed.get("entries", []):
                    if _e.get("type") == "unpublished_detected" and _e.get("id"):
                        _known_ids.add(_e["id"])
            except Exception:
                pass
        clist = [c for c in clist if c.get("id") not in _known_ids]
        if clist:
            _send_embed(title=f"Unpublished Content: {len(clist)}", description="",
                        fields=[{"name": "Items", "value": "\n".join(
                            f"#{c['id']} ({c['endpoint']}) HTTP {c['status']}" for c in clist[:10]
                        )[:1024]}], color=0xaa44ff)

    # External site changes
    ext_groups = {
        "external_dns_changed": ("DNS Changes", 0x4488ff),
        "external_robots_txt_changed": ("robots.txt Changes", 0x88ccff),
        "external_sitemap_changed": ("Sitemap Changes", 0x44aaff),
        "external_content_changed": ("External Content Changes", 0x66aaff),
        "external_unpublished_detected": ("Unpublished Content (External)", 0xaa44ff),
    }
    for type_key, (label, color) in ext_groups.items():
        if type_key in by_type:
            clist = by_type[type_key]
            fields = []
            for c in clist[:5]:
                site = c.get("site", "?")
                detail = c.get("detail", "")[:200]
                diff = c.get("diff", "")[:500]
                url = c.get("url", "")
                val = f"Site: {site}\n"
                if url:
                    val += f"<{url}>\n"
                if detail:
                    val += f"{detail}\n"
                if diff:
                    diff_clean = "\n".join(l for l in diff.split("\n") if not l.strip().startswith("# "))
                    if diff_clean:
                        val += f"```\n{diff_clean}\n```"
                fields.append({"name": label, "value": val[:1024]})
            if fields:
                _send_embed(title=f"{label}: {len(clist)}", description="", fields=fields[:10], color=color)

    # RecallDreams terminal commands -- one embed per entry, no truncation
    if "database_entry_changed" in by_type:
        from monitor.external_checker import iili_image_refs, grab_sync_images
        for c in by_type["database_entry_changed"]:
            entry_title = c.get("title", "(untitled)")
            slug = c.get("slug", "")
            date = c.get("date", "")[:10]
            content = c.get("content", "")
            kind = c.get("change_kind", "modified")
            val = f"**{kind.title()}**\n"
            if date:
                val += f"Date: {date}\n"
            if slug:
                val += f"Slug: {slug}\n"
            if content:
                val += f"```\n{content}\n```"

            # Archive + embed any iili.io sync screenshots referenced in the entry
            refs = iili_image_refs(content)
            hostname = c.get("site", "recalldreams.dev")
            site_state = state.setdefault("external", {}).setdefault(hostname, {})
            grabbed = grab_sync_images(refs, site_state)
            image_url = grabbed[0][1] if grabbed else None
            if refs:
                links = "\n".join(f"<{url}>" for _, url in refs)
                val += f"\n{links}"
            _send_embed(
                title=entry_title[:256],
                description="",
                fields=[{"name": "RecallDreams Terminal", "value": val[:1024]}],
                url=image_url,
                image=image_url,
                color=0x9966ff,
            )

    # YouTube uploads (Hello Games / No Man's Sky)
    yt_groups = {
        "external_youtube_video_public": ("New YouTube Upload", 0xff0033),
        "external_youtube_video_hidden": ("Hidden YouTube Upload", 0xcc0022),
        "external_youtube_video_removed": ("YouTube Video Removed", 0x881122),
        "external_youtube_count_changed": ("YouTube Count Changed", 0xff8800),
    }
    for type_key, (label, color) in yt_groups.items():
        if type_key not in by_type:
            continue
        for c in by_type[type_key]:
            title = c.get("title", "")
            url = c.get("url", "")
            desc = c.get("detail", "")
            fields = []
            if c.get("published"):
                fields.append({"name": "Published", "value": c["published"][:16]})
            if type_key == "external_youtube_count_changed":
                if c.get("total") is not None:
                    fields.append({"name": "Counts",
                                   "value": f"total: {c['total']}\npublic: {c.get('public', '?')}\nhidden: {c.get('hidden', '?')}"})
            elif c.get("title"):
                fields.append({"name": "Video", "value": f"<{url}>" if url else title})
            _send_embed(
                title=f"{label}: {title[:120]}" if title else label,
                description=desc[:256],
                fields=fields[:4],
                color=color,
                url=url or None,
                image=c.get("thumbnail") or None,
            )


def _resolve_author(user_map: dict, author_id) -> str:
    if not author_id:
        return ""
    return user_map.get(author_id, "")


def _discord_safe(text: str) -> str:
    """Neutralize < > in user text so they can't corrupt Discord's <url> link parsing."""
    return text.replace("<", "\uff1c").replace(">", "\uff1e")


def _format_added_item(i: dict, user_map: dict) -> str:
    """Format one api_items_added line. Comments show name + body + permalink."""
    if i.get("comment"):
        name = i.get("author_name") or _resolve_author(user_map, i.get("author", 0)) or "anonymous"
        val = f"#{i['id']}: {_discord_safe(name)}"
        body = i.get("comment", "")
        if body:
            val += f"\n{_discord_safe(body)}"
        link = i.get("link", "")
        if link:
            val += f"\n<{link}>"
        return val
    return f"#{i['id']}: {_resolve_author(user_map, i.get('author', 0))}: {_item_label(i)}"


def _item_label(item: dict) -> str:
    """Return an item label with a raw URL if available."""
    raw = item.get("title", "(untitled)")
    if isinstance(raw, dict):
        raw = raw.get("rendered", str(raw))
    title = str(raw)
    link = item.get("link", "")
    if link:
        return f"{title}\n<{link}>"
    return title


def _get_diff_preview(change: dict, item: dict = None) -> str:
    import html as html_mod
    import re
    from monitor.noise_filter import is_noise_diff_line

    diffs = change.get("diffs", [])
    if diffs:
        # Per-item diff selection: when an item is passed, only show the
        # diff that belongs to that item. The change dict carries diffs for
        # every modified item; without this, multi-item changes all show the
        # same (concatenated) preview dominated by the largest diff.
        if item is not None and item.get("id") is not None:
            iid = str(item.get("id"))
            matching = [
                d for d in diffs
                if str(d.get("url", "")).rstrip("/").endswith("/" + iid)
            ]
            if matching:
                diffs = matching

        lines_out = []
        for d in diffs:
            text_diff = d.get("text_diff")
            if text_diff:
                for line in text_diff.split("\n"):
                    if line.startswith("--- "):
                        continue
                    if line and line[0] in ("-", "+"):
                        if is_noise_diff_line(line):
                            continue
                        rest = line[1:].strip()
                        if rest:
                            lines_out.append(f"{line[0]} {rest}")
                continue
            raw_diff = d.get("diff", "")
            # Gallery-aware summary: tiled-gallery diffs are single JSON
            # lines of hundreds of KB. Tag-stripping leaves only newline
            # garbage, so instead compare the attachment ID sets directly.
            if "data-attachment-id=" in raw_diff:
                old_ids = set()
                new_ids = set()
                for line in raw_diff.split("\n"):
                    if not line or line[0] not in ("-", "+"):
                        continue
                    if "data-attachment-id=" in line:
                        ids = set(re.findall(r'data-attachment-id=\\?"(\d+)\\?"', line))
                        if line[0] == "-":
                            old_ids |= ids
                        else:
                            new_ids |= ids
                added = new_ids - old_ids
                removed = old_ids - new_ids
                parts = []
                if added:
                    parts.append(f"{len(added)} image(s) added")
                if removed:
                    parts.append(f"{len(removed)} image(s) removed")
                if not parts:
                    parts.append("edited")
                lines_out.append("+ gallery: " + ", ".join(parts))
                continue
            for line in raw_diff.split("\n"):
                line = line.rstrip("\r")
                if not line or line[0] == "@":
                    continue
                if is_noise_diff_line(line):
                    continue
                # Skip CSS noise in API diffs -- lines matching CSS rule patterns
                rest_check = line[1:].strip()
                if rest_check and (
                    rest_check.startswith('.') or
                    rest_check.startswith('#') or
                    rest_check.startswith('@media') or
                    rest_check.startswith(':root') or
                    rest_check.startswith('--wp-') or
                    ('{' in rest_check and ('color:' in rest_check or 'padding:' in rest_check or 'margin:' in rest_check or 'font-' in rest_check or 'border:' in rest_check or 'background:' in rest_check or 'display:' in rest_check or 'width:' in rest_check or 'height:' in rest_check))
                ):
                    continue
                prefix = line[0]
                rest = line[1:].strip()
                # Preserve link/media targets that live inside tags --
                # plain tag-stripping hides attribute-level changes
                # (e.g. an href gaining a cache-buster query param),
                # which made -/+ lines render as identical text.
                attrs = re.findall(
                    r'(?:href|src|poster|data-orig-file|data-large-file)="([^"]+)"',
                    rest,
                )
                attr_note = ""
                if attrs:
                    uniq = list(dict.fromkeys(attrs))
                    shown = " | ".join(a[:120] for a in uniq)
                    attr_note = f" [{shown}]"
                clean = re.sub(r'<[^>]+>', '', rest)
                if not clean and not attr_note:
                    continue
                lines_out.append(f"{prefix} {clean}{attr_note}")
        result = "\n".join(lines_out)
        if len(result) > 1000:
            result = result[:997] + "..."
        return result

    if change.get("content_changes") or change.get("type") == "api_items_modified":
        return ""

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
    lines = [l for l in lines if l and not re.match(r'^[.#@][\w-]+.*\{', l) and 'font-size:' not in l]
    result = "\n".join(lines)
    if len(result) > 500:
        result = result[:497] + "..."
    return result


def notify_trace_change(state: str, last_seen_at: str):
    if not DISCORD_WEBHOOKS:
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
