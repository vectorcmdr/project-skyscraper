"""Git push for docs/ -- stage, commit, push when site data changes."""

import subprocess

from monitor.config import MIRROR_DIR, SITE_DIR, GIT_BRANCH, GIT_USER_NAME, GIT_USER_EMAIL, GITHUB_TOKEN
from monitor.logger import log


def push_site():
    if not SITE_DIR.is_dir():
        log("git push skipped -- docs/ directory not found", "FILE")
        return

    try:
        r = subprocess.run(
            ["git", "add", "--all", str(SITE_DIR)],
            cwd=str(MIRROR_DIR), capture_output=True, text=True, timeout=30,
        )
        if r.returncode != 0:
            log(f"git add failed (exit={r.returncode}): {r.stderr.strip()}", "ERROR")
            return

        r = subprocess.run(
            ["git", "diff", "--cached", "--quiet"],
            cwd=str(MIRROR_DIR), capture_output=True, timeout=30,
        )
        if r.returncode == 0:
            log("git push skipped -- no site changes to commit", "FILE")
            return

        r = subprocess.run(
            ["git", "-c", f"user.name={GIT_USER_NAME}",
             "-c", f"user.email={GIT_USER_EMAIL}",
             "commit", "-m", "update site data"],
            cwd=str(MIRROR_DIR), capture_output=True, text=True, timeout=30,
        )
        if r.returncode != 0:
            log(f"git commit failed (exit={r.returncode}): {r.stderr.strip()}", "ERROR")
            return
        log(f"git commit: {r.stdout.strip()}", "FILE")

        remote_url = f"https://x-access-token:{GITHUB_TOKEN}@github.com/vectorcmdr/project-skyscraper.git" if GITHUB_TOKEN else "origin"
        r = subprocess.run(
            ["git", "push", remote_url, GIT_BRANCH],
            cwd=str(MIRROR_DIR), capture_output=True, text=True, timeout=60,
        )
        if r.returncode != 0:
            log(f"git push failed (exit={r.returncode}): {r.stderr.strip()}", "ERROR")
            return
        log(f"git push: {r.stdout.strip()}", "FILE")

    except FileNotFoundError:
        log("git push skipped -- git not found on PATH", "FILE")
    except subprocess.TimeoutExpired:
        log("git push timed out after 60s", "ERROR")
    except Exception as e:
        log(f"git push error: {e}", "ERROR")
