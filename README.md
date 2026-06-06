<h1 align="center">Project Skyscraper Change Monitor & Site Mirror</h1>
<h3 align="center"><i>
  
  for [project-skyscraper.com](https://project-skyscraper.com/)
  
</i></h3>
<div align="center" ><img src="https://github.com/vectorcmdr/project-skyscraper/blob/main/docs/favicon.jpg" width="160"></div>

<div align="center">
  
## Browse the monitor site: [External Operators Status Monitor](https://project-skyscraper.vectorcmdr.xyz/)

</div>

<br/>

_The static mirror of the Project Skyscraper ARG website is otherwise contained in the repo. The script to generate/update/serve is a fully self-discovering builder - no hardcoded IDs or URL lists._

---

> see `MIRROR_MANIFEST.md` for full listing

## Scripts

| File | Purpose |
|------|---------|
| `monitor/` | Modular Python package (shared logic across all tools) |
| `monitor_site.py` | Long-running change detection daemon (thin wrapper) |
| `start_monitor.ps1` | PowerShell launcher for `monitor_site.py` |
| `update_mirror.py` | Full mirror fetch, one-shot (thin wrapper) |
| `update_mirror.ps1` | PowerShell launcher for `update_mirror.py` |
| `serve_mirror.py` | Local HTTP server with URL rewriting |
| `config.json` | Configuration (webhook, git, polling intervals) |
| `docs/` | GitHub Pages site (status dashboard + changelog) |
| `state/monitor_state.json` | Persistent daemon state (auto-managed) |
| `diffs/` | Individual .diff files per changed resource |
| `MIRROR_MANIFEST.md` | Full mirror file listing (auto-generated) |
| `POST_ID_SERIES.md` | Post/page/media ID analysis + time reference table |
| `UNPUBLISHED_IDS.md` | Draft/private content IDs detected via probing |

Both `update_mirror.py` and `monitor_site.py` are thin entry points that import from the shared `monitor/` package. No duplicated logic.

---

## What's Mirrored (update_mirror.py)

| Category | Content |
|----------|---------|
| **HTML pages** | All sitemap URLs + root pages |
| **REST API (wp/v2)** | posts, pages, media, categories, tags, comments, users, blocks, navigation, menus, menu-items, sidebars, widgets, types, statuses, taxonomies, search, block-directory/search |
| **REST API (auth-gated)** | settings, themes, plugins, block-types, templates, template-parts, global-styles |
| **REST API (other namespaces)** | jetpack/v4, wpcom/v2/v3, wpcomsh/v1, akismet/v1, my-jetpack/v1, videopress/v1, boost, global-styles, help-center, wp-block-editor, wp-sync, code-snippets, crowdsignal-forms, wp-statistics, wp-site-health, wp-abilities, newspack-blocks |
| **Jetpack sub-endpoints** | site, module/*, scan/*, sync/*, connection/*, identity-crisis, plugins, recommendations, backup/*, stats-app, import, explat, blaze/*, videopress, social, search/*, verify-*, checkout, notice, etc. |
| **WP.com sub-endpoints** | /sites, /site-verticals, /block-likes |
| **oEmbed** | Every known page (JSON + XML) |
| **Media files** | Uploaded images, audio, files |
| **Theme assets** | style.css, theme.json, readme.txt, screenshot.png, CSS, JS, fonts |
| **Plugin assets** | Jetpack, Gutenberg, Gravatar, WP-Statistics CSS/JS |
| **Discovery docs** | robots.txt, sitemap-*.xml, sitemap-*.xsl |
| **Extras** | favicon.ico, readme.html, license.txt, xmlrpc.php, wp-config-sample.php, wp-admin assets |
| **Third-party CDN** | stats.wp.com, fonts.wp.com, s0.wp.com (analytics, fonts) |
| **External refs** | Reddit (r/NoMansSkyTheGame), Atlas-65 forum threads (mirrored only, not monitored) |

---

## Change Monitor (monitor_site.py)

A daemon that polls `project-skyscraper.com` in three tiers and alerts via Discord embeds when content changes.

### Polling Architecture

| Tier | Interval | Checks |
|------|----------|--------|
| **Fast** | 30s | Sitemap URL diff (new/removed pages only, not metadata) |
| **Medium** | 120s | All wp/v2 collections (conditional GET + paginated item diff) |
| **Deep** | 1800s | Page content hashes (15/cycle round-robin), media thumbnail ETags, unpublished ID probe (30 IDs/cycle) |

### Noise Suppression

Three-tier filtering prevents batcache, WP Statistics, nonce, and cache-timing noise from triggering alerts:

1. **Raw content stripping** -- removes known noise patterns from page content before hash comparison
2. **Beautified diff filtering** -- JS is beautified, JSON is indented, and diffs are checked for real structural changes
3. **Metadata-only API diffs** -- hash changes in API collections are inspected per-item; if only auto-generated fields (`_links`, `guid`, `meta`, etc.) differ, the change is suppressed

Noise-only changes still update the local mirror copy silently. Only genuine content additions, modifications, and removals produce Discord notifications and feed entries.

### First-Run / Restart

On restart or after `update_mirror.py` completes, the daemon detects a fresh state and performs a **quiet initial sync**: all content is fetched and mirrored, but no Discord pings, no feed.json updates, and no git pushes occur. The existing changelog and known-pages data is preserved.

### Locking

`update_mirror.py` and `monitor_site.py` share a PID-based lock file (`state/.monitor.lock`). Only one can run at a time; running the mirror update while the daemon is active will exit with an error. After `update_mirror.py` completes, it deletes the state file so the daemon quiet-syncs on next start.

### Trace (The Architect Online Status)

Polls the Discourse API every 60 seconds for `the_architect`'s `last_seen_at`. Writes `docs/status/trace.json` on state changes (ACTIVE if seen within 5 minutes, LOST otherwise). Transitions trigger a Discord embed (green for ACTIVE, red for LOST) and a git push to update the site.

### Discord Notifications

All alerts are sent as rich embeds with color-coded categories. The first embed in each cycle includes an @mention ping. Change types that trigger notifications:

- Sitemap URL added/removed (green)
- API items added (blue), modified (orange), removed (red)
- Page content changed (amber, with beautified diff preview)
- Media replaced (magenta), thumbnail changed (lavender), orphan upload (pink)
- Unpublished content detected (purple)
- Trace status ACTIVE/LOST (green/red)

### State

Persistent state stored in `state/monitor_state.json`:
- Per-endpoint ETags, hashes, item lists, pagination info
- Sitemap URL set with lastmod timestamps
- Media thumbnail variant ETags
- Orphan probe position (chunked sweep state)
- Stats counters (total checks, changes detected)

---

## Usage

```powershell
# Full mirror update (one-shot)
.\update_mirror.ps1
.\update_mirror.ps1 -Serve         # Update then start local server

# Change monitor daemon (long-running)
.\start_monitor.ps1
.\start_monitor.ps1 -Quiet         # Suppress routine log output

# Single check cycle (test/CI)
python monitor_site.py --check

# Serve existing mirror on :8080
python serve_mirror.py
```

All outputs are relative to the script directory. No external database required.
