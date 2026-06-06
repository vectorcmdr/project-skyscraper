"""Daemon orchestrator -- tiered polling loop for change detection."""

import signal
import sys
import time
import traceback
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

from monitor.config import (
    POLL_INTERVALS, COLLECTION_ENDPOINTS, MAX_WORKERS, PAGE_CHECK_CHUNK,
    MEANINGFUL_CHANGE_TYPES,
)
from monitor.logger import log
from monitor.state_manager import load_state, save_state, acquire_lock, release_lock
from monitor.sitemap import check_sitemap
from monitor.api_collections import check_api_collection, get_user_map
from monitor.page_checker import check_page_content, get_page_check_batch
from monitor.media_checker import check_media
from monitor.id_prober import probe_unpublished
from monitor.discord_notifier import notify_changes, notify_trace_change
from monitor.feed_manager import generate_site_data, seed_feed_from_mirror
from monitor.git_pusher import push_site
from monitor.trace_checker import check_trace, ensure_trace_default, init_trace_state
from monitor.report_writer import clean_old_reports, write_monitor_report
from monitor.discovery import fetch_and_save


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

    if "fast" in tiers:
        log("=== Fast check ===", "FAST")
        try:
            changes = check_sitemap(state)
            all_changes.extend(changes)
        except Exception as e:
            log(f"Error checking sitemap: {e}", "ERROR")

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

    save_state(state)

    if all_changes:
        state["stats"]["total_changes_detected"] += len(all_changes)

        if is_first_cycle:
            log(f"=== Initial sync: {len(all_changes)} change(s) -- mirroring quietly ===", "FETCH")
            _apply_changes(all_changes)
        else:
            log(f"=== Processing {len(all_changes)} change(s) ===", "FETCH")
            _apply_changes(all_changes)
            notify_changes(all_changes, state)
            generate_site_data(state, all_changes)
            meaningful = [c for c in all_changes if c.get("type") in MEANINGFUL_CHANGE_TYPES]
            if meaningful:
                push_site()
            else:
                log(f"All {len(all_changes)} change(s) noise-only -- skipping git push", "CHECK")
    else:
        log("No changes detected", "CHECK")

    return all_changes


def _apply_changes(changes: list):
    for change in changes:
        ctype = change["type"]

        if ctype == "sitemap_added":
            for page_url in change.get("urls", []):
                if page_url.startswith("https://project-skyscraper.com"):
                    fetch_and_save(page_url, "html")
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
                        fetch_and_save(link, "html")
                        time.sleep(0.2)
                elif "/pages" in endpoint:
                    fetch_and_save(f"https://project-skyscraper.com/wp-json/wp/v2/pages/{iid}", "api")
                    time.sleep(0.2)
                    link = item.get("link", "")
                    if link and link.startswith("https://project-skyscraper.com"):
                        fetch_and_save(link, "html")
                        time.sleep(0.2)
                elif "/media" in endpoint:
                    fetch_and_save(f"https://project-skyscraper.com/wp-json/wp/v2/media/{iid}", "api")
                    time.sleep(0.2)
                    media_url = item.get("url") or item.get("source_url", "")
                    if media_url:
                        fetch_and_save(media_url, "media")
                        time.sleep(0.2)

        elif ctype == "api_items_modified":
            from monitor.diff_engine import compute_diff
            from monitor.url_mapper import url_to_path

            endpoint = change.get("endpoint", "")
            for item in change.get("items", []):
                iid = item.get("id")
                if not iid:
                    continue
                if "/posts" in endpoint:
                    _diff_and_save(f"https://project-skyscraper.com/wp-json/wp/v2/posts/{iid}", "api", change)
                    time.sleep(0.2)
                    link = item.get("link", "")
                    if link and link.startswith("https://project-skyscraper.com"):
                        _diff_and_save(link, "html", change)
                        time.sleep(0.2)
                elif "/pages" in endpoint:
                    _diff_and_save(f"https://project-skyscraper.com/wp-json/wp/v2/pages/{iid}", "api", change)
                    time.sleep(0.2)
                    link = item.get("link", "")
                    if link and link.startswith("https://project-skyscraper.com"):
                        _diff_and_save(link, "html", change)
                        time.sleep(0.2)

        elif ctype == "page_content_changed":
            page_url = change.get("url", "")
            if page_url:
                fetch_and_save(page_url, "html")
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


def _diff_and_save(url: str, subdir: str, change_obj: dict):
    from monitor.diff_engine import compute_diff, compute_text_diff
    from monitor.url_mapper import url_to_path

    path = url_to_path(url, subdir=subdir)
    old_bytes = path.read_bytes() if path.is_file() else None
    fetch_and_save(url, subdir)
    new_bytes = path.read_bytes() if path.is_file() else None
    if old_bytes is not None and new_bytes is not None and old_bytes != new_bytes:
        diff = compute_diff(old_bytes, new_bytes, url, str(path.relative_to(path.parents[3])) if path.parents else "")
        entry = {"url": url, "diff": diff if diff else ""}
        if "wp-json" in url:
            text_diff = compute_text_diff(old_bytes, new_bytes)
            if text_diff:
                entry["text_diff"] = text_diff
        change_obj.setdefault("diffs", []).append(entry)


def daemon_loop(quiet: bool = False):
    if not acquire_lock():
        log("Cannot acquire lock.", "ERROR")
        sys.exit(1)

    print_banner()
    state = load_state()
    save_state(state)
    seed_feed_from_mirror(state)
    ensure_trace_default()
    init_trace_state()

    last_tiers = {"fast": 0, "medium": 0, "deep": 0}

    is_first = state["stats"]["total_checks"] == 0
    log("Starting initial check cycle..." + (" (quiet sync)" if is_first else ""))
    run_check_cycle(state, tiers={"fast", "medium", "deep"}, is_initial=is_first)
    log("Initial check complete")

    def _on_shutdown(signum, frame):
        log("Shutting down...")
        save_state(state)
        release_lock()
        log("Monitor stopped")
        sys.exit(0)

    signal.signal(signal.SIGINT, _on_shutdown)
    signal.signal(signal.SIGTERM, _on_shutdown)

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

            trace_changed = check_trace()
            if trace_changed:
                import json
                from monitor.config import TRACE_STATUS_FILE
                try:
                    td = json.loads(TRACE_STATUS_FILE.read_text(encoding="utf-8"))
                    notify_trace_change(td.get("state", "LOST"), td.get("lastSeenAt", ""))
                except Exception:
                    pass
                push_site()

            if now % 3600 < 1:
                clean_old_reports()

            time.sleep(1)

        except Exception as e:
            log(f"Daemon loop error: {e}", "ERROR")
            traceback.print_exc()
            time.sleep(10)


def run_single_check():
    if not acquire_lock():
        log("Cannot acquire lock.", "ERROR")
        sys.exit(1)

    try:
        print_banner()
        state = load_state()
        save_state(state)
        ensure_trace_default()
        init_trace_state()

        is_first = state["stats"]["total_checks"] == 0
        log("Single check mode" + (" (quiet sync)" if is_first else ""))
        run_check_cycle(state, tiers={"fast", "medium", "deep"}, is_initial=is_first)

        trace_changed = check_trace()
        if trace_changed:
            import json
            from monitor.config import TRACE_STATUS_FILE
            try:
                td = json.loads(TRACE_STATUS_FILE.read_text(encoding="utf-8"))
                notify_trace_change(td.get("state", "LOST"), td.get("lastSeenAt", ""))
            except Exception:
                pass
            push_site()

        log("Check complete")
    finally:
        save_state(state)
        release_lock()
