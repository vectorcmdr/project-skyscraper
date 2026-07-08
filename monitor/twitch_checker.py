"""Twitch live/offline checker — polls Helix API for project_skyscraper stream status."""

import json
import time
import urllib.parse
from datetime import datetime, timezone

from monitor.http_client import fetch
from monitor.logger import log
from monitor.config import TWITCH_CLIENT_ID, TWITCH_CLIENT_SECRET, TWITCH_STATUS_FILE, TWITCH_POLL_INTERVAL

_TWITCH_USER = "project_skyscraper"
_TOKEN_URL = "https://id.twitch.tv/oauth2/token"
_STREAMS_URL = f"https://api.twitch.tv/helix/streams?user_login={_TWITCH_USER}"

_twitch_last_state = None
_twitch_access_token = None
_twitch_last_poll = 0


def init_twitch_state():
    global _twitch_last_state
    if TWITCH_STATUS_FILE.is_file():
        try:
            data = json.loads(TWITCH_STATUS_FILE.read_text(encoding="utf-8"))
            _twitch_last_state = data.get("state")
        except Exception:
            pass


def ensure_twitch_default():
    if not TWITCH_STATUS_FILE.is_file():
        TWITCH_STATUS_FILE.parent.mkdir(parents=True, exist_ok=True)
        TWITCH_STATUS_FILE.write_text(json.dumps({
            "state": "OFFLINE",
            "updatedAt": "2026-05-06T18:31:49+00:00",
        }, indent=2), encoding="utf-8")
        log("Twitch: wrote default twitch.json", "FILE")


def _get_access_token() -> str | None:
    if not TWITCH_CLIENT_ID or not TWITCH_CLIENT_SECRET:
        log("Twitch: missing client_id or client_secret in config.json", "WARN")
        return None
    data = urllib.parse.urlencode({
        "client_id": TWITCH_CLIENT_ID,
        "client_secret": TWITCH_CLIENT_SECRET,
        "grant_type": "client_credentials",
    }).encode()
    result = fetch(_TOKEN_URL, method="POST", data=data, timeout=15)
    if result.failed:
        log(f"Twitch: token request failed ({result.status})", "WARN")
        return None
    try:
        body = json.loads(result.text)
        return body.get("access_token")
    except (json.JSONDecodeError, KeyError) as e:
        log(f"Twitch: failed to parse token response ({e})", "WARN")
        return None


def check_twitch() -> str | bool:
    global _twitch_last_state, _twitch_access_token, _twitch_last_poll

    try:
        now_ts = time.time()
        if now_ts - _twitch_last_poll < TWITCH_POLL_INTERVAL:
            return False
        _twitch_last_poll = now_ts

        if not _twitch_access_token:
            _twitch_access_token = _get_access_token()
            if not _twitch_access_token:
                return False

        headers = {
            "Client-Id": TWITCH_CLIENT_ID,
            "Authorization": f"Bearer {_twitch_access_token}",
        }
        result = fetch(_STREAMS_URL, headers_extra=headers)

        if result.status == 401:
            log("Twitch: token expired, refreshing", "TWITCH")
            _twitch_access_token = _get_access_token()
            if not _twitch_access_token:
                return False
            headers["Authorization"] = f"Bearer {_twitch_access_token}"
            result = fetch(_STREAMS_URL, headers_extra=headers)

        if result.failed:
            log(f"Twitch: stream fetch failed ({result.status})", "WARN")
            return False

        try:
            body = json.loads(result.text)
            is_live = len(body.get("data", [])) > 0
        except (json.JSONDecodeError, KeyError) as e:
            log(f"Twitch: failed to parse stream response ({e})", "WARN")
            return False

        new_state = "LIVE" if is_live else "OFFLINE"
        now = datetime.now(timezone.utc).isoformat()

        if new_state == _twitch_last_state:
            return "updated"

        _twitch_last_state = new_state
        TWITCH_STATUS_FILE.parent.mkdir(parents=True, exist_ok=True)
        TWITCH_STATUS_FILE.write_text(json.dumps({
            "state": new_state,
            "updatedAt": now,
        }, indent=2), encoding="utf-8")

        log(f"Twitch state changed: {new_state}", "INFO")
        return "changed"

    except BaseException:
        return False
