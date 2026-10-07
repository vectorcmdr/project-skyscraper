"""YouTube channel upload tracking -- public uploads, hidden uploads, counts.

Monitors a YouTube channel for No Man's Sky / Hello Games upload activity:

* RSS feed (cheap, ~26 KB): the 15 most recent *public* uploads. A new video
  in the feed means a video was just made available to the public -- notify
  immediately with the watch link and thumbnail.
* Uploads playlist (UU<channel_id>, via the mobile-web browse API): includes
  unlisted/hidden uploads that never appear on the public Videos tab or in
  the RSS feed. New playlist entries that are not in the public set are
  hidden uploads.
* About page (mobile HTML): the authoritative public video count
  ("107 videos"), compared against the playlist total to detect count
  mismatches (e.g. "108 total, 107 public, 1 hidden").

All three run on the fast tier (30 s default) so upload movement is captured
immediately. No YouTube API key required.
"""

import hashlib
import json
import re
import time
from datetime import datetime, timezone

from monitor.config import (
    YOUTUBE_SITES,
    YOUTUBE_RSS_INTERVAL,
    YOUTUBE_PLAYLIST_INTERVAL,
    YOUTUBE_COUNTS_INTERVAL,
    YOUTUBE_CHANNEL_BROWSE_BASE,
)
from monitor.http_client import fetch
from monitor.logger import log

_MOBILE_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 Mobile/15E148 Safari/604.1"
)

_RSS_ENTRY_RE = re.compile(r"<entry>(.*?)</entry>", re.S)
_RSS_VIDEO_ID_RE = re.compile(r"<yt:videoId>([^<]+)</yt:videoId>")
_RSS_TITLE_RE = re.compile(r"<title>([^<]*)</title>")
_RSS_LINK_RE = re.compile(r'<link rel="alternate" href="([^"]+)"')
_RSS_PUBLISHED_RE = re.compile(r"<published>([^<]+)</published>")
_RSS_THUMB_RE = re.compile(r'<media:thumbnail url="([^"]+)"')

# public count from the About page ("107 videos")
_ABOUT_VIDEO_COUNT_RE = re.compile(r'"videoCountText"\s*:\s*"?([\d,]+)\s*videos?')
_ABOUT_SUB_RE = re.compile(r'"subscriberCountText"\s*:\s*"?([^"]+)"')


def check_youtube(state: dict) -> list:
    """Check all configured YouTube channels. Fast tier (30s)."""
    changes = []
    yt_state = state.setdefault("youtube", {})
    stats = state.setdefault("stats", {})

    for key, info in YOUTUBE_SITES.items():
        channel_state = yt_state.setdefault(key, {})
        channel_id = info.get("channel_id", "")
        site_label = info.get("label", key)
        if not channel_id:
            continue

        try:
            changes.extend(_check_youtube_rss(channel_id, channel_state, stats, site_label))
        except Exception as e:
            log(f"  YouTube RSS check failed for {key}: {e}", "ERROR")
        try:
            changes.extend(_check_youtube_playlist(channel_id, channel_state, stats, site_label))
        except Exception as e:
            log(f"  YouTube playlist check failed for {key}: {e}", "ERROR")
        try:
            changes.extend(_check_youtube_counts(channel_id, channel_state, stats, site_label))
        except Exception as e:
            log(f"  YouTube counts check failed for {key}: {e}", "ERROR")

        channel_state["last_checked"] = datetime.now(timezone.utc).isoformat()

    return changes


# ---------------------------------------------------------------------------
# Sub-checks
# ---------------------------------------------------------------------------

def _check_youtube_rss(channel_id: str, cs: dict, stats: dict, label: str) -> list:
    """RSS feed: detect new public uploads and videos removed from public."""
    if not _due(stats, "_youtube_rss_last", YOUTUBE_RSS_INTERVAL):
        return []
    _stamp(stats, "_youtube_rss_last")

    url = f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"
    result = fetch(url, headers_extra={"Accept-Encoding": "identity"})
    if result.failed or not result.content:
        log(f"  YouTube RSS fetch failed ({result.status})", "WARN")
        return []

    entries = _parse_rss(result.content.decode("utf-8", errors="replace"))
    if not entries:
        return []

    rss_state = cs.setdefault("rss", {})
    known = rss_state.get("videos", {})  # videoId -> {title, link, published, first_seen}
    new_videos = {e["video_id"]: e for e in entries}

    changes = []
    if not known:
        # First run: quiet baseline
        rss_state["videos"] = {vid: {**e, "first_seen": _now_iso()} for vid, e in new_videos.items()}
        rss_state["last_updated"] = _now_iso()
        log(f"  YouTube RSS baseline: {len(new_videos)} public videos", "CHECK")
        return changes

    # Stale-feed guard: under rapid polling YouTube's RSS endpoint can
    # intermittently serve an old cached feed (or, rarely, a foreign
    # channel's). A fresh feed always still contains the previously-known
    # newest video -- unless the channel uploaded something newer, in
    # which case the feed's #1 entry is newer than the known newest. If
    # neither holds, the response is stale/foreign: skip the cycle
    # entirely so it can never re-announce old videos, corrupt the known
    # set, or flip the hidden/public playlist logic.
    known_newest_id = max(known, key=lambda vid: known[vid].get("published", ""))
    known_newest_pub = known[known_newest_id].get("published", "")
    stale = known_newest_id not in new_videos
    if stale and known_newest_pub:
        feed_newest = max(new_videos.values(), key=lambda e: e.get("published", ""))
        stale = feed_newest.get("published", "") <= known_newest_pub
    if stale:
        log("  YouTube RSS: stale/foreign feed response (known newest missing) -- skipping", "WARN")
        return changes

    added_ids = [vid for vid in new_videos if vid not in known]
    removed_ids = [vid for vid in known if vid not in new_videos]

    for vid in added_ids:
        e = new_videos[vid]
        changes.append({
            "type": "external_youtube_video_public",
            "site": "youtube",
            "site_label": label,
            "url": e.get("link", f"https://www.youtube.com/watch?v={vid}"),
            "video_id": vid,
            "title": e.get("title", ""),
            "published": e.get("published", ""),
            "thumbnail": e.get("thumbnail", ""),
            "diff": f"+ {e.get('title', vid)} (published {e.get('published', '?')[:10]})",
            "detail": f"New public upload: {e.get('title', '')[:120]}",
        })
        log(f"  YouTube: new public video {vid} ({e.get('title', '')[:60]})", "CHECK")

    # Videos that left the RSS window. These are only *candidates* for
    # removal: the RSS feed carries the ~15 newest public uploads, so a
    # video can leave the window simply by aging out. The playlist check
    # confirms real removals when the playlist total also drops.
    left_public = rss_state.setdefault("left_public", {})
    for vid in removed_ids:
        rec = known[vid]
        try:
            first = datetime.fromisoformat(rec.get("first_seen", "").replace("Z", "+00:00"))
            age = (datetime.now(timezone.utc) - first).total_seconds()
        except Exception:
            age = 0
        if age >= YOUTUBE_RSS_INTERVAL * 2:
            left_public[vid] = {
                "title": rec.get("title", ""),
                "link": rec.get("link", f"https://www.youtube.com/watch?v={vid}"),
                "left_at": _now_iso(),
            }
            log(f"  YouTube: video left public feed (candidate) {vid}", "DEEP")

    rss_state["videos"] = {vid: {**e, "first_seen": known.get(vid, {}).get("first_seen", _now_iso())}
                           for vid, e in new_videos.items()}
    rss_state["last_updated"] = _now_iso()
    return changes


def _check_youtube_playlist(channel_id: str, cs: dict, stats: dict, label: str) -> list:
    """Uploads playlist (UU<channel_id>): total count + hidden/unlisted uploads."""
    if not _due(stats, "_youtube_playlist_last", YOUTUBE_PLAYLIST_INTERVAL):
        return []
    _stamp(stats, "_youtube_playlist_last")

    total, playlist_vids = _fetch_playlist(channel_id)
    if total is None:
        log("  YouTube playlist fetch failed", "WARN")
        return []

    pl_state = cs.setdefault("playlist", {})
    known_total = pl_state.get("total_count")
    known_hidden = pl_state.get("hidden_videos", {})  # videoId -> {title?, first_seen}

    # Public set = union of RSS-known + playlist-known-non-hidden
    rss_state = cs.setdefault("rss", {})
    public_ids = set(rss_state.get("videos", {}).keys())

    # The RSS feed only carries the ~15 newest *public* uploads, so only the
    # first len(public_ids) playlist positions can be compared against it.
    # Any video in that newest window that is not public is a hidden upload.
    # Older playlist positions may be public videos simply outside the RSS
    # window -- not hidden.
    window = min(len(public_ids), len(playlist_vids)) if public_ids else 0
    changes = []
    new_hidden = []
    for vid, title in playlist_vids[:window]:
        if vid not in public_ids and vid not in known_hidden:
            new_hidden.append((vid, title))

    if known_total is None:
        # First run: quiet baseline. Use the same RSS-window rule as the
        # steady-state path so baseline-hidden matches what we'd detect.
        pl_state["total_count"] = total
        pl_state["hidden_videos"] = {vid: {"title": t, "first_seen": _now_iso()}
                                     for vid, t in playlist_vids[:window] if vid not in public_ids}
        pl_state["last_updated"] = _now_iso()
        log(f"  YouTube playlist baseline: total={total}", "CHECK")
        return changes

    if known_total != total:
        changes.append({
            "type": "external_youtube_count_changed",
            "site": "youtube",
            "site_label": label,
            "url": "https://www.youtube.com/channel/" + channel_id,
            "old_total": known_total,
            "new_total": total,
            "diff": f"- total: {known_total}\n+ total: {total}",
            "detail": f"YouTube upload total: {known_total} -> {total}",
        })
        log(f"  YouTube: total count {known_total} -> {total}", "CHECK")

        # A playlist total drop confirms removals: candidates that left the
        # RSS window are confirmed gone when the playlist total also shrank.
        # (Videos that merely aged out of the RSS 15-window keep the total
        # unchanged and stay unreported.)
        if total < known_total:
            left_public = rss_state.setdefault("left_public", {})
            for vid in list(left_public):
                rec = left_public.pop(vid)
                changes.append({
                    "type": "external_youtube_video_removed",
                    "site": "youtube",
                    "site_label": label,
                    "url": rec.get("link", f"https://www.youtube.com/watch?v={vid}"),
                    "video_id": vid,
                    "title": rec.get("title", ""),
                    "diff": f"- {rec.get('title', vid)} (removed)",
                    "detail": f"Video removed: {rec.get('title', '')[:120]}",
                })
                log(f"  YouTube: confirmed removed {vid} (total dropped)", "CHECK")

    for vid, title in new_hidden:
        changes.append({
            "type": "external_youtube_video_hidden",
            "site": "youtube",
            "site_label": label,
            "url": f"https://www.youtube.com/watch?v={vid}",
            "video_id": vid,
            "title": title or "",
            "diff": f"+ hidden upload: {title or vid}",
            "detail": f"Hidden upload detected: {title or vid}",
        })
        known_hidden[vid] = {"title": title, "first_seen": _now_iso()}
        log(f"  YouTube: hidden upload {vid} ({title or ''})", "CHECK")

    # A hidden video that went public moves from hidden set to public set
    promoted = [vid for vid in list(known_hidden) if vid in public_ids]
    for vid in promoted:
        del known_hidden[vid]
        log(f"  YouTube: hidden video {vid} is now public", "CHECK")

    pl_state["total_count"] = total
    pl_state["hidden_videos"] = known_hidden
    pl_state["last_updated"] = _now_iso()
    return changes


def _check_youtube_counts(channel_id: str, cs: dict, stats: dict, label: str) -> list:
    """About page: authoritative public video count vs playlist total."""
    if not _due(stats, "_youtube_counts_last", YOUTUBE_COUNTS_INTERVAL):
        return []
    _stamp(stats, "_youtube_counts_last")

    count_state = cs.setdefault("counts", {})
    public_count = _fetch_public_count(channel_id)
    if public_count is None:
        log("  YouTube About page fetch failed", "WARN")
        return []

    known_public = count_state.get("public_count")
    if known_public is None:
        # First run: quiet baseline. Absorb the pre-existing mismatch (if
        # any) so startup doesn't fire on long-hidden videos.
        count_state["public_count"] = public_count
        total = cs.setdefault("playlist", {}).get("total_count")
        if total is not None and total != public_count:
            count_state["last_mismatch"] = total - public_count
        count_state["last_updated"] = _now_iso()
        log(f"  YouTube count baseline: public={public_count}", "CHECK")
        return []

    changes = []
    if known_public != public_count:
        changes.append({
            "type": "external_youtube_count_changed",
            "site": "youtube",
            "site_label": label,
            "url": "https://www.youtube.com/channel/" + channel_id,
            "old_public": known_public,
            "new_public": public_count,
            "diff": f"- public: {known_public}\n+ public: {public_count}",
            "detail": f"YouTube public video count: {known_public} -> {public_count}",
        })
        log(f"  YouTube: public count {known_public} -> {public_count}", "CHECK")

    # Total vs public mismatch (hidden videos)
    total = cs.setdefault("playlist", {}).get("total_count")
    if total is not None and total != public_count:
        hidden = total - public_count
        last_mismatch = count_state.get("last_mismatch")
        if last_mismatch != hidden:
            changes.append({
                "type": "external_youtube_count_changed",
                "site": "youtube",
                "site_label": label,
                "url": "https://www.youtube.com/channel/" + channel_id,
                "total": total,
                "public": public_count,
                "hidden": hidden,
                "diff": f"= total: {total}\n= public: {public_count}\n= hidden: {hidden}",
                "detail": f"YouTube count mismatch: {total} total, {public_count} public, {hidden} hidden",
            })
            count_state["last_mismatch"] = hidden
            log(f"  YouTube: count mismatch ({hidden} hidden)", "CHECK")

    count_state["public_count"] = public_count
    count_state["last_updated"] = _now_iso()
    return changes


# ---------------------------------------------------------------------------
# Fetching / parsing helpers
# ---------------------------------------------------------------------------

def _fetch_playlist(channel_id: str):
    """Fetch the uploads playlist (UU<channel_id>).

    Returns (total_count, [(video_id, title), ...]) for the newest entries
    (the playlist is newest-first, so hidden uploads appear near the top).
    total_count is None on failure.
    """
    # Uploads playlist browseId: "VL" + uploads playlist id, where the
    # uploads playlist is "UU" + channel id *without* the "UC" prefix.
    # e.g. UCGKx5... -> UUGKx5... -> browseId VLUUGKx5...
    playlist_id = ("UU" + channel_id[2:]) if channel_id.startswith("UC") else ("UU" + channel_id)
    browse_id = "VL" + playlist_id
    # Try the MWEB client first (compact JSON); the WEB client as fallback
    # (MWEB browse intermittently 400s under rapid anonymous requests).
    # Both are unauthenticated.
    attempts = (
        ("MWEB", "2.20240801.00.00", _MOBILE_UA),
        ("WEB", "2.20240801.00.00", None),
    )
    for idx, (client_name, client_version, ua) in enumerate(attempts):
        if idx > 0:
            time.sleep(1.5)
        body = json.dumps({
            "context": {"client": {"clientName": client_name, "clientVersion": client_version, "hl": "en"}},
            "browseId": browse_id,
        }).encode("utf-8")
        headers_extra = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if ua:
            headers_extra["User-Agent"] = ua
        result = fetch(
            YOUTUBE_CHANNEL_BROWSE_BASE,
            method="POST",
            data=body,
            headers_extra=headers_extra,
        )
        if result.ok and result.content:
            parsed = _parse_playlist_response(result.text)
            if parsed[0] is not None or parsed[1]:
                return parsed
    return None, []


def _parse_playlist_response(text: str):
    """Extract (total_count, [(video_id, title), ...]) from a playlist browse response."""
    total = None
    m = re.search(r'"numVideosText"\s*:\s*\{[^}]*?"text"\s*:\s*"([\d,]+)"', text)
    if m:
        total = int(m.group(1).replace(",", ""))

    # Walk the JSON: playlist items are {"videoId": "...", ...} with a
    # nearby title. MWEB uses accessibilityContext.label
    # ("<Title> by <Channel> <views> <age> <duration>"); WEB uses
    # {"title": {"runs": [{"text": "..."}]}}. Try both. Dedupe by videoId
    # (the same id appears in multiple JSON contexts).
    vids = []
    seen = set()
    for m in re.finditer(r'"videoId"\s*:\s*"([0-9A-Za-z_-]{11})"', text):
        vid = m.group(1)
        if vid in seen:
            continue
        seen.add(vid)
        start = m.start()
        title = ""
        before = re.search(r'"title"\s*:\s*\{\s*"runs"\s*:\s*\[\s*\{\s*"text"\s*:\s*"((?:[^"\\]|\\.)*)"',
                           text[max(0, start - 500):start])
        if before:
            title = _unescape_js(before.group(1))
        if not title:
            label_m = re.search(r'"accessibilityContext"\s*:\s*\{\s*"label"\s*:\s*"([^"]*?)\s+by\s+[^"]*"',
                                text[max(0, start - 1500):start])
            if label_m:
                title = _unescape_js(label_m.group(1))
        vids.append((vid, title))

    return total, vids


def _fetch_public_count(channel_id: str):
    """Public video count from the channel About page.

    Uses the desktop page: its ytInitialData is plain JSON
    ("videoCountText":"107 videos"), unlike the mobile page which
    escape-encodes quotes as \\x22. gzip cuts the wire size ~5x.
    """
    url = f"https://www.youtube.com/channel/{channel_id}/about"
    result = fetch(url, headers_extra={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36",
        "Accept-Encoding": "gzip, deflate",
    })
    if result.failed or not result.content:
        return None
    text = result.text
    m = _ABOUT_VIDEO_COUNT_RE.search(text)
    if not m:
        return None
    return int(m.group(1).replace(",", ""))


def _parse_rss(text: str) -> list:
    entries = []
    for entry_m in _RSS_ENTRY_RE.finditer(text):
        e = entry_m.group(1)
        vid_m = _RSS_VIDEO_ID_RE.search(e)
        if not vid_m:
            continue
        title_m = _RSS_TITLE_RE.search(e)
        link_m = _RSS_LINK_RE.search(e)
        pub_m = _RSS_PUBLISHED_RE.search(e)
        thumb_m = _RSS_THUMB_RE.search(e)
        entries.append({
            "video_id": vid_m.group(1),
            "title": title_m.group(1) if title_m else "",
            "link": link_m.group(1) if link_m else "",
            "published": pub_m.group(1) if pub_m else "",
            "thumbnail": thumb_m.group(1) if thumb_m else "",
        })
    return entries


def _unescape_js(s: str) -> str:
    try:
        return json.loads('"' + s + '"')
    except Exception:
        return s


def _due(stats: dict, key: str, interval: int) -> bool:
    last = stats.get(key, 0)
    return (time.time() - last) >= interval


def _stamp(stats: dict, key: str):
    stats[key] = time.time()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
