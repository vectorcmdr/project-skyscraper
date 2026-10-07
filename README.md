<!-- github pages build bumper: 5 -->

<h1 align="center">Project Skyscraper Change Monitor</h1>
<h3 align="center"><i>
  
  for [project-skyscraper.com](https://project-skyscraper.com/)
  
</i></h3>
<div align="center" ><img src="https://github.com/vectorcmdr/project-skyscraper/blob/main/docs/favicon.jpg" width="160"></div>

<div align="center">
  
## Browse the monitor site: [External Operators Status Monitor](https://project-skyscraper.vectorcmdr.xyz/)

</div>

<br/>

_24/7 change detection for the Project Skyscraper ARG: the main site, its REST API, and the external sites surrounding it. Runs on GitHub Actions, alerts to Discord, and publishes the Ops Centre dashboard via GitHub Pages._

---

## How it works

The monitor runs on GitHub-hosted runners, once every ~10 minutes:

- `.github/workflows/monitor.yml` runs `python monitor_site.py --check` on a schedule (plus manual dispatch from the Actions tab).
- Each run checks everything: the sitemap, all monitored REST API collections, a rotating batch of page content, media files, unpublished-ID probes, and the external sites.
- Detected changes are posted to Discord as rich embeds and written to the dashboard feed (`docs/data/feed.json`).
- State (ETags, hashes, item lists, probe positions) is committed back at the end of the run, so the next run picks up where the last left off. Exactly one commit per run (`update site data`).

The same code still runs locally as a long-lived daemon if desired (see [Running locally](#running-locally)). GitHub's scheduler may delay runs a few minutes under load; detection is state-based, so late runs still catch everything that changed.

## What is monitored

| Target | Checks |
|--------|--------|
| **project-skyscraper.com** | Sitemap URL set; 18 wp/v2 collections (posts, pages, media, comments, users, menus, navigation, blocks, ...); rotating page-content batch; media files and thumbnails; unpublished/draft content via 401 probes |
| **recalldreams.dev** | Page content (HTML signatures), WP collections, sitemap, robots.txt, DNS records, sync screenshots (archived at detection time) |
| **wakingtitan.com** | Content hashes, robots.txt, DNS records |
| **freeimage.host** | Upload activity |
| **YouTube** | Project Skyscraper channel feed |

### The Architect (trace)

Each run polls the Atlas-65 Discourse API for `the_architect`'s `last_seen_at`. Status is `ACTIVE` when seen within 20 minutes, otherwise `LOST`. Transitions produce a Discord alert and update `docs/status/trace.json` (written only when the state actually changes).

## Meaningful-change filtering

A change only notifies when it is real content. Four layers keep noise out:

1. **Raw stripping** - 40+ patterns remove batcache timestamps, statistics pings, nonces, cache-busters, and ad-hoc inline CSS before comparison
2. **Beautified diffs** - JS is beautified and JSON re-indented so formatting churn doesn't register
3. **Content signatures** - external pages are compared by their visible content/structure signature, not raw HTML
4. **Two-strike confirmation** - API item removals and content flips must persist across consecutive checks before they alert

Noise-only changes still refresh the stored copies silently. On a fresh state (first run or after state removal), a quiet baseline is captured - no notifications until something genuinely changes.

## Discord notifications

Rich, color-coded embeds - the first embed of a cycle tags the configured operator. Categories include: sitemap URL added/removed, API items added/modified/removed, page content changed (with inline diff preview), media replaced, orphan uploads, unpublished content detected, external-site changes, trace ACTIVE/LOST transitions, and RecallDreams terminal entries (with the sync screenshot embedded when one is available).

## Repository layout

| Path | Purpose |
|------|---------|
| `.github/workflows/monitor.yml` | Hosted monitor workflow (schedule + manual dispatch) |
| `monitor_site.py` | Entry point: `--check` for a single cycle, or the local daemon loop |
| `monitor/` | Monitor package: detection, noise filtering, notifiers, publishing |
| `docs/` | GitHub Pages dashboard (Ops Centre) and its change-feed data |
| `state/` | Persistent monitor state (committed by each run) |
| `html/`, `external/` | Stored page bodies used as content-diff baselines |
| `api/wp-json/wp/v2/` | Structural API snapshots used by the neural-net graph builder |
| `monitor_reports/collections/` | Point-in-time API collection snapshots retained with changes |
| `config.example.json` | Configuration template (`config.json` is gitignored) |

## Configuration

**Hosted (GitHub Actions):** repository secrets `DISCORD_WEBHOOK` and `DISCORD_PING_ID`. Every setting can be overridden with a `PS_*` environment variable; the workflow sets `PS_HOSTED=1`.

**Local:** copy `config.example.json` to `config.json` (gitignored) and fill in the Discord webhook, ping ID, and GitHub token.

## Running locally

```powershell
# Single check cycle (safe test / CI)
python monitor_site.py --check

# Long-running local daemon (30s / 120s / 1800s tiers)
python monitor_site.py
```

All outputs are relative to the script directory. No external database required.

---

## License

> Mirror content is not owned by me and is either public domain (fair use) as preserved web content, or the property of the rights holder, or both. This is for you to determine and is not in any way legal advice.

> The backup and utilities are made with respect and love for the NMS community, Hello Games and Puppet Master running the ARG. Any issues, please reach out and I will address them as soon as I am able.

> The GNU AGPL v3 license within the repo covers the tracking site and all of ti's contents, the diffs, self made metada, json feeds, patches and glue pieces, etc. used to structure the backup and monitoring site + tools, as well as any other original conten not included in the original site and it's owned contents - it does not lay claim over the other pieces of recovered mirror content that are a part of project-skyscraper.
