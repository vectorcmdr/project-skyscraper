"""Daemon orchestrator -- tiered polling loop for change detection."""

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
)
from monitor.logger import log
from monitor.state_manager import load_state, save_state, acquire_lock, release_lock
from monitor.sitemap import check_sitemap
from monitor.api_collections import check_api_collection, get_user_map
from monitor.page_checker import check_page_content, get_page_check_batch
from monitor.media_checker import check_media
from monitor.id_prober import probe_unpublished
from monitor.discord_notifier import notify_changes, notify_trace_change
from monitor.feed_manager import generate_site_data, generate_external_data, seed_feed_from_mirror
from monitor.graph_builder import build_graph, rebuild_on_change, write_graph
from monitor.git_pusher import push_site
from monitor.trace_checker import check_trace, ensure_trace_default, init_trace_state
from monitor.report_writer import clean_old_reports, write_monitor_report, refresh_reports
from monitor.discovery import fetch_and_save, fetch_protected_page
from monitor.external_checker import check_external_sites


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

        try:
            changes = check_external_sites(state)
            all_changes.extend(changes)
        except Exception as e:
            log(f"Error checking external sites: {e}", "ERROR")

    if all_changes:
        state["stats"]["total_changes_detected"] += len(all_changes)

        if quiet:
            log(f"=== Initial sync: {len(all_changes)} change(s) -- mirroring quietly ===", "FETCH")
            _apply_changes(all_changes, state=state)
            save_state(state)
            # Always process new/removed items even during warmup
            always_capture = [c for c in all_changes if c.get("type") in ("api_items_added", "api_items_removed", "sitemap_added", "external_dns_changed", "external_robots_txt_changed", "external_content_changed", "external_unpublished_detected")]
            if always_capture:
                notify_changes(always_capture, state)
                generate_site_data(state, always_capture)
                generate_external_data(state, always_capture)
        else:
            log(f"=== Processing {len(all_changes)} change(s) ===", "FETCH")
            _apply_changes(all_changes, state=state)
            save_state(state)
            notify_changes(all_changes, state)
            generate_site_data(state, all_changes)
            generate_external_data(state, all_changes)
            rebuild_on_change(all_changes, state)
            meaningful = [c for c in all_changes if c.get("type") in MEANINGFUL_CHANGE_TYPES]
            if meaningful:
                push_site()
            else:
                log(f"All {len(all_changes)} change(s) noise-only -- skipping git push", "CHECK")
    else:
        save_state(state)
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
                        time.sleep(0.2)
                elif "/pages" in endpoint:
                    fetch_and_save(f"https://project-skyscraper.com/wp-json/wp/v2/pages/{iid}", "api")
                    time.sleep(0.2)
                    link = item.get("link", "")
                    if link and link.startswith("https://project-skyscraper.com"):
                        _fetch_page_html(link)
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
                        _diff_and_save(link, "html", change, state=state)
                        time.sleep(0.2)
                elif "/pages" in endpoint:
                    _diff_and_save(f"https://project-skyscraper.com/wp-json/wp/v2/pages/{iid}", "api", change)
                    time.sleep(0.2)
                    link = item.get("link", "")
                    if link and link.startswith("https://project-skyscraper.com"):
                        _diff_and_save(link, "html", change, state=state)
                        time.sleep(0.2)

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


def daemon_loop(quiet: bool = False):
    if not acquire_lock():
        log("Cannot acquire lock.", "ERROR")
        sys.exit(1)

    print_banner()
    state = load_state()
    _sync_state_hashes_to_mirror(state)
    state["stats"]["_warmup"] = 2
    save_state(state)

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

                trace_changed = check_trace()
                if trace_changed:
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
    if not acquire_lock():
        log("Cannot acquire lock.", "ERROR")
        sys.exit(1)

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

        trace_changed = check_trace()
        if trace_changed:
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

        log("Check complete")
    finally:
        save_state(state)
        release_lock()
