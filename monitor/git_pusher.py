"""Git push for docs/ -- stage, commit, push when site data changes."""

import subprocess
import time

from monitor.config import MIRROR_DIR, SITE_DIR, GIT_BRANCH, GIT_USER_NAME, GIT_USER_EMAIL, GITHUB_TOKEN
from monitor.logger import log


def _git_retry(cmd, timeout, retries=6, delay=1):
    for attempt in range(retries):
        try:
            r = subprocess.run(
                cmd, cwd=str(MIRROR_DIR), capture_output=True, text=True, timeout=timeout,
            )
            if r.returncode == 0:
                return r
            stderr = r.stderr.strip()
            is_lock = r.returncode == 128 and any(
                kw in stderr for kw in ["index.lock", "Unable to create", "could not open"]
            )
            if is_lock and attempt < retries - 1:
                log(f"git lock contention (attempt {attempt+1}/{retries}), retrying in {delay}s", "WARN")
                time.sleep(delay)
                delay *= 2
                continue
            return r
        except subprocess.TimeoutExpired as e:
            if attempt < retries - 1:
                log(f"git timed out (attempt {attempt+1}/{retries}), retrying in {delay}s", "WARN")
                time.sleep(delay)
                delay *= 2
                continue
            raise
    return None


def push_site():
    if not SITE_DIR.is_dir():
        log("git push skipped -- docs/ directory not found", "FILE")
        return

    try:
        label = "git add"
        r = _git_retry(["git", "add", "--all", str(SITE_DIR)], timeout=30)
        if r and r.returncode != 0:
            log(f"{label} failed (exit={r.returncode}): {r.stderr.strip()}", "ERROR")
            return
        if not r:
            return

        label = "git diff"
        r = _git_retry(["git", "diff", "--cached", "--quiet"], timeout=30)
        if r and r.returncode == 0:
            log("git push skipped -- no site changes to commit", "FILE")
            return
        if not r:
            return

        label = "git commit"
        r = _git_retry(
            ["git", "-c", f"user.name={GIT_USER_NAME}",
             "-c", f"user.email={GIT_USER_EMAIL}",
             "commit", "-m", "update site data"],
            timeout=30,
        )
        if r and r.returncode != 0:
            log(f"{label} failed (exit={r.returncode}): {r.stderr.strip()}", "ERROR")
            return
        if not r:
            return
        log(f"git commit: {r.stdout.strip()}", "FILE")

        label = "git push"
        remote_url = f"https://x-access-token:{GITHUB_TOKEN}@github.com/vectorcmdr/project-skyscraper.git" if GITHUB_TOKEN else "origin"
        r = _git_retry(["git", "push", remote_url, GIT_BRANCH], timeout=60)
        if r and r.returncode != 0:
            log(f"{label} failed (exit={r.returncode}): {r.stderr.strip()}", "ERROR")
            return
        if not r:
            return
        log(f"git push: {r.stdout.strip()}", "FILE")

    except FileNotFoundError:
        log("git push skipped -- git not found on PATH", "FILE")
    except subprocess.TimeoutExpired:
        log("git push timed out after 60s", "ERROR")
    except Exception as e:
        log(f"git push error: {e}", "ERROR")
