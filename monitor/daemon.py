"""Daemon orchestrator -- tiered polling loop for change detection."""

import re
import signal
import sys
import time
import traceback
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone


if sys.platform == "win32":
    import ctypes
    _kernel32 = ctypes.windll.kernel32
    _CTRL_C_EVENT = 0
    _handler_t = ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_uint)
    def _console_handler(dwCtrlType):
        if dwCtrlType == _CTRL_C_EVENT:
            return 0
        return 1
    _console_handler_cb = _handler_t(_console_handler)
    _kernel32.SetConsoleCtrlHandler(_console_handler_cb, 1)

from monitor.config import (
    POLL_INTERVALS, COLLECTION_ENDPOINTS, MAX_WORKERS, PAGE_CHECK_CHUNK,
    MEANINGFUL_CHANGE_TYPES, DATA_DIR, PASSWORD_PROTECTED_PAGES,
    RECALLDREAMS_MIRROR_INTERVAL, BASE_URL, HOSTED,
)
from monitor.logger import log
from monitor.state_manager import load_state, save_state, acquire_lock, release_lock
from monitor.sitemap import check_sitemap
from monitor.api_collections import check_api_collection, get_user_map
from monitor.page_checker import check_page_content, get_page_check_batch
from monitor.config import STALE_BYPASS_URLS
from monitor.media_checker import check_media
from monitor.id_prober import probe_unpublished
from monitor.discord_notifier import notify_changes, notify_trace_change
from monitor.feed_manager import generate_site_data, generate_external_data, seed_feed_from_mirror
from monitor.graph_builder import build_graph, rebuild_on_change, write_graph
from monitor.git_pusher import push_site as _push_site
from monitor.trace_checker import check_trace, ensure_trace_default, init_trace_state
from monitor.report_writer import clean_old_reports, write_monitor_report, refresh_reports
from monitor.discovery import fetch_and_save, fetch_protected_page
from monitor.external_checker import check_external_sites
from monitor.noise_filter import PATTERN_VERSION

# Hosted single-push mode: run_single_check defers every internal push_site
# call and performs one final push when the whole check has finished, so a
# hosted run produces at most one commit instead of one commit per phase
# (startup graph, applied changes, trace). Local daemon behavior unchanged.
_push_deferred = False


def push_site():
    if _push_deferred:
        return
    _push_site()

try:
    from mirror_recalldreams import mirror_recalldreams
    _HAS_RECALL_MIRROR = True
except ImportError:
    _HAS_RECALL_MIRROR = False


def _sync_state_hashes_to_mirror(state: dict):
    import hashlib
    from monitor.url_mapper import url_to_path
    pages = state.get("pages", {})
    synced = 0
    for url, ps in pages.items():
        path = url_to_path(url, "html")
        if path.is_file():
            fh = hashlib.md5(path.read_bytes()).hexdigest()
            if ps.get("hash") != fh:
                ps["hash"] = fh
                synced += 1
    if synced:
        log(f"Synced {synced} stale state hashes to mirror", "FILE")


def _diff_is_cache_blip(c: dict, d: dict) -> bool:
    """True when a diff belongs to an item tagged as a cache-staleness
    blip (its modified timestamp flipped to a previously-seen state)."""
    items = c.get("items") or []
    blip_ids = {str(i.get("id")) for i in items if i.get("cache_blip")}
    if not blip_ids:
        return False
    url = d.get("url", "")
    endpoint = c.get("endpoint", "")
    for i in items:
        iid = str(i.get("id"))
        if iid not in blip_ids:
            continue
        if url == f"{BASE_URL}{endpoint}/{iid}" or url == i.get("link", ""):
            return True
    return False


def _change_is_noise_only(c: dict) -> bool:
    """True when every diff produced for a change reduces to pure noise
    (e.g. the /neural-network-status/ live Inbound Attempts counter) or
    belongs to an item tagged as a cache-staleness blip."""
    from monitor.noise_filter import diff_has_real_changes

    diffs = c.get("diffs") or []
    if not diffs and c.get("diff"):
        diffs = [{"diff": c["diff"]}]
    if not diffs:
        items = c.get("items") or []
        if items and all(i.get("cache_blip") for i in items):
            return True
        return False
    for d in diffs:
        if _diff_is_cache_blip(c, d):
            continue
        text = d.get("text_diff")
        if text is None:
            text = d.get("diff") or ""
        if diff_has_real_changes(text):
            return False
    return True


def _check_new_page_memory_bloc(url: str, change: dict):
    import re
    from monitor.url_mapper import url_to_path
    html_path = url_to_path(url, "html")
    if not html_path.is_file():
        return
    try:
        text = html_path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return
    m = re.search(r'Memory_bloc_restoration:\s*(\d+/\d+)', text)
    if not m:
        return
    new_val = m.group(1)
    diff_text = f"- Memory_bloc_restoration: 0/365\n+ Memory_bloc_restoration: {new_val}"
    change.setdefault("diffs", []).append({"url": url, "diff": diff_text})


def _run_recalldreams_mirror_if_due(state: dict, force: bool = False) -> list:
    if not _HAS_RECALL_MIRROR:
        return []
    # Hosted (GitHub Actions) runs skip the full recalldreams mirror: its
    # archival role needs persistent bodies that ephemeral runners do not
    # have, and its notification role is covered by the external checker
    # (page content signatures, 2h collection checks, sitemap, probes).
    from monitor.config import HOSTED
    if HOSTED:
        return []
    now = time.time()
    if not force:
        last = state.get("stats", {}).get("_recalldreams_last_mirror", 0)
        if now - last < RECALLDREAMS_MIRROR_INTERVAL:
            return []
    log("=== recalldreams.dev mirror ===", "MIRROR")
    site_state = state.setdefault("external", {}).setdefault("recalldreams.dev", {})
    stats, changes = mirror_recalldreams(site_state)
    state.setdefault("stats", {})["_recalldreams_last_mirror"] = now
    log(f"  mirror result: fetched={stats['fetched']} new={stats['new']} changed={stats['changed']}", "MIRROR")
    return changes


def print_banner():
    print(flush=True)
    print("  project-skyscraper.com - Change Monitor", flush=True)
    print(f"  Intervals: fast={POLL_INTERVALS['fast']}s  "
          f"medium={POLL_INTERVALS['medium']}s  "
          f"deep={POLL_INTERVALS['deep']}s", flush=True)
    print(flush=True)
    print("  Press Ctrl+C to stop", flush=True)
    print(flush=True)


def run_check_cycle(state: dict, tiers: set = None, is_initial: bool = False) -> list:
    all_changes = []

    if tiers is None:
        tiers = {"fast", "medium", "deep"}

    is_first_cycle = state["stats"]["total_checks"] == 0
    state["stats"]["total_checks"] += 1
    state["stats"]["last_run"] = datetime.now(timezone.utc).isoformat()
    if state["stats"]["first_run"] is None:
        state["stats"]["first_run"] = state["stats"]["last_run"]

    warmup = state["stats"].get("_warmup", 0)
    quiet = is_initial or is_first_cycle or warmup > 0
    if warmup > 0:
        state["stats"]["_warmup"] = warmup - 1

    if is_initial or is_first_cycle:
        try:
            mirror_changes = _run_recalldreams_mirror_if_due(state, force=True)
            all_changes.extend(mirror_changes)
        except Exception as e:
            log(f"Error on initial recalldreams mirror: {e}", "ERROR")

    if "fast" in tiers:
        log("=== Fast check ===", "FAST")
        try:
            changes = check_sitemap(state)
            all_changes.extend(changes)
        except Exception as e:
            log(f"Error checking sitemap: {e}", "ERROR")

        try:
            for path in STALE_BYPASS_URLS:
                url = f"{BASE_URL}{path}"
                changes = check_page_content(url, state)
                all_changes.extend(changes)
        except Exception as e:
            log(f"Error checking priority page: {e}", "ERROR")

        try:
            from monitor.external_checker import check_external_wp_pages
            all_changes.extend(check_external_wp_pages(state))
        except Exception as e:
            log(f"Error checking external WP pages: {e}", "ERROR")

        try:
            from monitor.external_checker import check_external_db_entries
            all_changes.extend(check_external_db_entries(state))
        except Exception as e:
            log(f"Error checking external DB entries: {e}", "ERROR")

        try:
            from monitor.external_checker import check_freeimage
            all_changes.extend(check_freeimage(state))
        except Exception as e:
            log(f"Error checking freeimage.host: {e}", "ERROR")

        try:
            from monitor.youtube_checker import check_youtube
            all_changes.extend(check_youtube(state))
        except Exception as e:
            log(f"Error checking YouTube: {e}", "ERROR")

    if "medium" in tiers:
        log("=== Medium check ===", "MEDIUM")
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
            futures = {}
            for ep in COLLECTION_ENDPOINTS:
                futures[ex.submit(check_api_collection, ep, state)] = ep

            for f in as_completed(futures):
                ep = futures[f]
                try:
                    changes = f.result()
                    all_changes.extend(changes)
                except Exception as e:
                    log(f"Error checking {ep}: {e}", "ERROR")

    if "deep" in tiers:
        log("=== Deep check ===", "DEEP")

        page_urls = get_page_check_batch(state, PAGE_CHECK_CHUNK)
        if page_urls:
            with ThreadPoolExecutor(max_workers=3) as ex:
                futures = {}
                for page_url in page_urls:
                    futures[ex.submit(check_page_content, page_url, state)] = page_url

                for f in as_completed(futures):
                    try:
                        changes = f.result()
                        all_changes.extend(changes)
                    except Exception as e:
                        log(f"Error checking page: {e}", "ERROR")

        try:
            changes = check_media(state)
            all_changes.extend(changes)
        except Exception as e:
            log(f"Error checking media: {e}", "ERROR")

        try:
            changes = probe_unpublished(state)
            all_changes.extend(changes)
        except Exception as e:
            log(f"Error probing unpublished: {e}", "ERROR")

        try:
            changes = check_external_sites(state)
            all_changes.extend(changes)
        except Exception as e:
            log(f"Error checking external sites: {e}", "ERROR")

        try:
            mirror_changes = _run_recalldreams_mirror_if_due(state)
            all_changes.extend(mirror_changes)
        except Exception as e:
            log(f"Error mirroring recalldreams.dev: {e}", "ERROR")

    # === Quiet re-baseline on noise-pattern-set changes ===
    # strip_page_noise invalidates every stored external hash when the pattern
    # set changes. Absorb the resulting one-time hash-mismatch changes silently
    # (the check functions already refreshed state baselines during the run).
    if state["stats"].get("_noise_version") != PATTERN_VERSION:
        state["stats"]["_noise_version"] = PATTERN_VERSION
        absorbed = [
            c for c in all_changes
            if c.get("type") in ("external_content_changed", "database_entry_changed")
        ]
        for c in absorbed:
            all_changes.remove(c)
        log(
            f"Noise pattern set v{PATTERN_VERSION}: quiet re-baseline "
            f"(absorbed {len(absorbed)} external change(s), "
            f"{len(all_changes)} remaining)",
            "CHECK",
        )

    if all_changes:
        state["stats"]["total_changes_detected"] += len(all_changes)

        # === Memory-bloc wave quiescence (see plan section 3.5) ===
        # 1. Write classification log for every api_items_modified cycle
        # 2. Advance STATE_A for non-anomalous cycles
        # 3. Record stats for silent cycles
        # 4. Partition changes into silent and alert
        from monitor.wave_stats import (
            log_classification, record_normal_cycle,
            should_emit_rollup, reset_after_rollup,
        )
        from monitor.state_a import put as state_a_put
        from monitor.cycle_classifier import is_silent
        from monitor.config import WAVE_SILENT_NORMAL

        for c in all_changes:
            if c.get("type") != "api_items_modified":
                continue
            log_classification(c)
            endpoint = c.get("endpoint", "")
            snapshot = c.get("snapshot")
            cycle_type = c.get("cycle_type", "")
            if endpoint and snapshot:
                state_a_put(endpoint, c.get("ts", ""), snapshot, cycle_type)

        if WAVE_SILENT_NORMAL:
            silent_changes = [
                c for c in all_changes
                if c.get("type") == "api_items_modified"
                and is_silent(c.get("cycle_type", ""))
            ]
            for c in silent_changes:
                record_normal_cycle()
            alert_changes = [c for c in all_changes if c not in silent_changes]
        else:
            silent_changes = []
            alert_changes = list(all_changes)

        log(
            f"=== Processing {len(alert_changes)} alert change(s), "
            f"{len(silent_changes)} silent ===",
            "FETCH",
        )

        _apply_changes(all_changes, state=state)
        save_state(state)

        if quiet:
            always_capture = [
                c for c in alert_changes
                if c.get("type") in (
                    "api_items_added", "api_items_removed",
                    "sitemap_added", "database_entry_changed",
                    "external_dns_changed", "external_robots_txt_changed",
                    "external_sitemap_changed", "external_content_changed",
                    "external_unpublished_detected",
                )
            ]
            if always_capture:
                notify_changes(always_capture, state)
                generate_site_data(state, always_capture)
                generate_external_data(state, always_capture)
        else:
            notify_changes(alert_changes, state)
            generate_site_data(state, alert_changes)
            generate_external_data(state, alert_changes)
            rebuild_on_change(alert_changes, state)
            meaningful = [
                c for c in alert_changes
                if c.get("type") in MEANINGFUL_CHANGE_TYPES
                and not _change_is_noise_only(c)
            ]
            rollup_due = should_emit_rollup() if WAVE_SILENT_NORMAL else False
            if meaningful or rollup_due:
                push_site()
                if rollup_due and not meaningful:
                    log(
                        f"Roll-up commit fired "
                        f"(no alert changes this period, "
                        f"threshold reached)",
                        "ROLLUP",
                    )
                if rollup_due:
                    reset_after_rollup()
            else:
                log(
                    f"All {len(alert_changes)} alert change(s) noise-only -- "
                    f"skipping git push",
                    "CHECK",
                )
    else:
        # No changes this cycle. Still check roll-up cadence.
        from monitor.wave_stats import should_emit_rollup, reset_after_rollup
        from monitor.config import WAVE_SILENT_NORMAL
        feed_written = generate_site_data(state, [])
        save_state(state)
        rollup_due = should_emit_rollup() if WAVE_SILENT_NORMAL else False
        if feed_written or rollup_due:
            try:
                push_site()
                if rollup_due and not feed_written:
                    log("Roll-up commit fired (quiet period, threshold reached)", "ROLLUP")
                if rollup_due:
                    reset_after_rollup()
            except BaseException:
                log("No-changes push failed, will retry next cycle", "WARN")
        log("No changes detected", "CHECK")

    return all_changes


def _apply_changes(changes: list, state: dict = None):
    for change in changes:
        ctype = change["type"]

        if ctype == "sitemap_added":
            for page_url in change.get("urls", []):
                if page_url.startswith("https://project-skyscraper.com"):
                    _fetch_page_html(page_url)
                    time.sleep(0.3)

        elif ctype == "api_items_added":
            endpoint = change.get("endpoint", "")
            for item in change.get("items", []):
                iid = item.get("id")
                if not iid:
                    continue
                if "/posts" in endpoint:
                    fetch_and_save(f"https://project-skyscraper.com/wp-json/wp/v2/posts/{iid}", "api")
                    time.sleep(0.2)
                    link = item.get("link", "")
                    if link and link.startswith("https://project-skyscraper.com"):
                        _fetch_page_html(link)
                        _check_new_page_memory_bloc(link, change)
                        time.sleep(0.2)
                elif "/pages" in endpoint:
                    fetch_and_save(f"https://project-skyscraper.com/wp-json/wp/v2/pages/{iid}", "api")
                    time.sleep(0.2)
                    link = item.get("link", "")
                    if link and link.startswith("https://project-skyscraper.com"):
                        _fetch_page_html(link)
                        _check_new_page_memory_bloc(link, change)
                        time.sleep(0.2)
                elif "/media" in endpoint:
                    fetch_and_save(f"https://project-skyscraper.com/wp-json/wp/v2/media/{iid}", "api")
                    time.sleep(0.2)
                    media_url = item.get("url") or item.get("source_url", "")
                    if media_url:
                        fetch_and_save(media_url, "media")
                        time.sleep(0.2)

        elif ctype == "api_items_modified":
            endpoint = change.get("endpoint", "")

            snap_prev = change.get("snapshot_prev")
            snap_cur = change.get("snapshot")
            if snap_prev and snap_cur:
                _diff_items_from_snapshots(change, snap_prev, snap_cur)
            else:
                for item in change.get("items", []):
                    iid = item.get("id")
                    if not iid:
                        continue
                    if "/posts" in endpoint:
                        _diff_and_save(f"https://project-skyscraper.com/wp-json/wp/v2/posts/{iid}", "api", change)
                        time.sleep(0.2)
                        link = item.get("link", "")
                        if link and link.startswith("https://project-skyscraper.com"):
                            _diff_and_save(link, "html", change, state=state)
                            _check_new_page_memory_bloc(link, change)
                            time.sleep(0.2)
                    elif "/pages" in endpoint:
                        _diff_and_save(f"https://project-skyscraper.com/wp-json/wp/v2/pages/{iid}", "api", change)
                        time.sleep(0.2)
                        link = item.get("link", "")
                        if link and link.startswith("https://project-skyscraper.com"):
                            _diff_and_save(link, "html", change, state=state)
                            _check_new_page_memory_bloc(link, change)
                            time.sleep(0.2)
                _flag_dropped_captures(change)

        elif ctype == "page_content_changed":
            page_url = change.get("url", "")
            if page_url:
                _fetch_page_html(page_url)
                time.sleep(0.3)

        elif ctype == "media_thumbnail_changed":
            thumb_url = change.get("url", "")
            if thumb_url:
                fetch_and_save(thumb_url, "media")
                time.sleep(0.15)

        elif ctype == "media_replaced":
            new_url = change.get("new_url", "")
            if new_url:
                fetch_and_save(new_url, "media")
                time.sleep(0.2)
            mid = change.get("id")
            if mid:
                fetch_and_save(f"https://project-skyscraper.com/wp-json/wp/v2/media/{mid}", "api")
                time.sleep(0.15)

    write_monitor_report("changes", {"count": len(changes), "changes": changes[:50]})


def _fetch_page_html(url: str, subdir: str = "html"):
    password = PASSWORD_PROTECTED_PAGES.get(url)
    if password:
        fetch_protected_page(url, password, subdir)
    else:
        fetch_and_save(url, subdir)


def _diff_and_save(url: str, subdir: str, change_obj: dict, state: dict = None):
    from monitor.diff_engine import compute_diff, compute_text_diff
    from monitor.url_mapper import url_to_path
    import hashlib

    path = url_to_path(url, subdir=subdir)
    old_bytes = path.read_bytes() if path.is_file() else None
    if subdir == "html":
        _fetch_page_html(url)
    else:
        fetch_and_save(url, subdir)
    new_bytes = path.read_bytes() if path.is_file() else None
    if old_bytes is not None and new_bytes is not None and old_bytes != new_bytes:
        diff = compute_diff(old_bytes, new_bytes, url, str(path.relative_to(path.parents[3])) if path.parents else "")
        if not diff:
            return
        entry = {"url": url, "diff": diff}
        if "wp-json" in url:
            text_diff = compute_text_diff(old_bytes, new_bytes)
            if text_diff:
                entry["text_diff"] = text_diff
        change_obj.setdefault("diffs", []).append(entry)

    if subdir == "html" and state is not None:
        page_state = state.setdefault("pages", {}).setdefault(url, {})
        page_state["hash"] = hashlib.md5(new_bytes).hexdigest()
        page_state["last_checked"] = datetime.now(timezone.utc).isoformat()
        page_state["etag"] = None
        page_state["last_modified"] = None


def _diff_items_from_snapshots(change: dict, snap_prev: str, snap_cur: str) -> bool:
    """Diff api_items_modified against two collection snapshots (offline).

    The live site can revert before per-item fetches run (bulk content-only
    waves like the 07-31 memory bloc shifts), so the detection-time snapshot
    is the only reliable record of the shifted values. Also writes the
    detection-time item JSON into the mirror so the local mirror reflects
    what the site served when the change was seen.
    """
    import json
    from pathlib import Path as _Path

    from monitor.diff_engine import compute_diff, compute_text_diff
    from monitor.url_mapper import url_to_path

    try:
        prev = json.loads(_Path(snap_prev).read_text(encoding="utf-8")).get("items", [])
        cur = json.loads(_Path(snap_cur).read_text(encoding="utf-8")).get("items", [])
    except Exception as e:
        log(f"Snapshot diff: cannot read snapshots ({e}), falling back to live fetch", "WARN")
        return False

    prev_map = {str(i.get("id")): i for i in prev if i.get("id") is not None}
    cur_map = {str(i.get("id")): i for i in cur if i.get("id") is not None}

    endpoint = change.get("endpoint", "")
    count_found = 0
    count_diffed = 0
    missing = []
    for item in change.get("items", []):
        iid = item.get("id")
        if iid is None:
            continue
        old_item = prev_map.get(str(iid))
        new_item = cur_map.get(str(iid))
        if old_item is None or new_item is None:
            missing.append({"id": iid, "url": "", "reason": "not found in snapshots"})
            continue
        count_found += 1
        if "/posts" in endpoint:
            api_url = f"https://project-skyscraper.com/wp-json/wp/v2/posts/{iid}"
        else:
            api_url = f"https://project-skyscraper.com/wp-json/wp/v2/pages/{iid}"
        old_bytes = json.dumps(old_item, indent=2, ensure_ascii=False).encode("utf-8")
        new_bytes = json.dumps(new_item, indent=2, ensure_ascii=False).encode("utf-8")
        path = url_to_path(api_url, subdir="api")
        if path:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(new_bytes)
        diff = compute_diff(old_bytes, new_bytes, api_url,
                            str(path.relative_to(path.parents[3])) if path and path.parents else "")
        if not diff:
            continue
        entry = {"url": api_url, "diff": diff}
        text_diff = compute_text_diff(old_bytes, new_bytes)
        if text_diff:
            entry["text_diff"] = text_diff
        change.setdefault("diffs", []).append(entry)
        count_diffed += 1

    if missing:
        change.setdefault("not_captured", []).extend(missing)
        log(f"WARN: {len(missing)} item(s) missing from snapshots for {endpoint}", "WARN")
    log(f"API {endpoint}: snapshot-diffed {count_diffed}/{len(change.get('items', []))} "
        f"item(s) from {_Path(snap_cur).name}", "MEDIUM")
    return True


def _flag_dropped_captures(change: dict):
    """Flag content_changed items that produced no diff (reverted mid-fetch)."""
    endpoint = change.get("endpoint", "")
    missing = []
    diff_urls = {d.get("url") for d in change.get("diffs", [])}
    for item in change.get("items", []):
        if not item.get("content_changed"):
            continue
        iid = item.get("id")
        if iid is None:
            continue
        if "/posts" in endpoint:
            api_url = f"https://project-skyscraper.com/wp-json/wp/v2/posts/{iid}"
        else:
            api_url = f"https://project-skyscraper.com/wp-json/wp/v2/pages/{iid}"
        if api_url not in diff_urls:
            missing.append({"id": iid, "url": api_url})
    if missing:
        change.setdefault("not_captured", []).extend(missing)
        log(f"WARN: {len(missing)} content_changed item(s) had no diff at fetch time "
            f"(likely reverted mid-fetch): {endpoint}", "WARN")


def daemon_loop(quiet: bool = False):
    if not acquire_lock():
        log("Cannot acquire lock.", "ERROR")
        sys.exit(1)

    print_banner()
    state = load_state()
    _sync_state_hashes_to_mirror(state)
    state["stats"]["_warmup"] = 2
    save_state(state)

    seed_feed_from_mirror(state)

    graph_path = DATA_DIR / "graph.json"
    if not graph_path.is_file():
        log("Seeding graph.json from mirror data...", "FILE")
        write_graph(build_graph(state))
    else:
        log("Refreshing graph.json...", "FILE")
        write_graph(build_graph(state))

    ensure_trace_default()
    init_trace_state()
    try:
        push_site()
    except BaseException:
        log("Startup push_site interrupted, continuing", "WARN")

    last_tiers = {"fast": 0, "medium": 0, "deep": 0}

    log("Starting initial sync cycle (quiet)...")
    run_check_cycle(state, tiers={"fast", "medium", "deep"}, is_initial=True)
    log("Initial sync complete, now monitoring")

    def _on_shutdown(signum, frame):
        log("Shutting down...")
        save_state(state)
        release_lock()
        log("Monitor stopped")
        sys.exit(0)

    signal.signal(signal.SIGINT, _on_shutdown)

    try:
        while True:
            try:
                now = time.time()
                tiers_to_run = set()

                if now - last_tiers["fast"] >= POLL_INTERVALS["fast"]:
                    tiers_to_run.add("fast")
                    last_tiers["fast"] = now

                if now - last_tiers["medium"] >= POLL_INTERVALS["medium"]:
                    tiers_to_run.add("medium")
                    last_tiers["medium"] = now

                if now - last_tiers["deep"] >= POLL_INTERVALS["deep"]:
                    tiers_to_run.add("deep")
                    last_tiers["deep"] = now

                if tiers_to_run:
                    run_check_cycle(state, tiers=tiers_to_run)

                trace_result = check_trace()
                if trace_result == "changed":
                    import json
                    from monitor.config import TRACE_STATUS_FILE
                    try:
                        td = json.loads(TRACE_STATUS_FILE.read_text(encoding="utf-8"))
                        notify_trace_change(td.get("state", "LOST"), td.get("lastSeenAt", ""))
                    except Exception:
                        pass
                    try:
                        push_site()
                    except BaseException:
                        pass
                elif trace_result == "updated":
                    # lastSeenAt genuinely moved (Architect activity) --
                    # publish immediately, same as state transitions.
                    try:
                        push_site()
                    except BaseException:
                        pass

                if now % 3600 < 1:
                    clean_old_reports()
                    refresh_reports(state)

                time.sleep(1)

            except Exception as e:
                log(f"Daemon loop error: {e}", "ERROR")
                traceback.print_exc()
                time.sleep(10)
            except BaseException as e:
                log(f"Daemon loop FATAL: {type(e).__name__}: {e}", "ERROR")
                if not (isinstance(e, SystemExit) and e.code == 0):
                    traceback.print_exc()
                raise
    finally:
        release_lock()
        log("Daemon loop exited, lock released")


def run_single_check():
    global _push_deferred
    if not acquire_lock():
        log("Cannot acquire lock.", "ERROR")
        sys.exit(1)

    if HOSTED:
        _push_deferred = True
    try:
        print_banner()
        state = load_state()
        _sync_state_hashes_to_mirror(state)
        save_state(state)
        ensure_trace_default()
        init_trace_state()

        log("Single check mode")
        write_graph(build_graph(state))
        try:
            push_site()
        except BaseException:
            pass
        run_check_cycle(state, tiers={"fast", "medium", "deep"})

        trace_result = check_trace()
        if trace_result == "changed":
            import json
            from monitor.config import TRACE_STATUS_FILE
            try:
                td = json.loads(TRACE_STATUS_FILE.read_text(encoding="utf-8"))
                notify_trace_change(td.get("state", "LOST"), td.get("lastSeenAt", ""))
            except Exception:
                pass
            try:
                push_site()
            except BaseException:
                pass
        elif trace_result == "updated":
            try:
                push_site()
            except BaseException:
                pass

        log("Check complete")
    finally:
        save_state(state)
        if HOSTED:
            # Single push for the whole hosted run: everything staged
            # during the check goes out as at most one commit.
            _push_deferred = False
            try:
                push_site()
            except BaseException:
                log("Final hosted push failed, will retry next run", "WARN")
        release_lock()
