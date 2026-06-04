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
| `update_mirror.py` | Full 12-phase mirror fetch (one-shot) |
| `update_mirror.ps1` | PowerShell wrapper for `update_mirror.py` |
| `monitor_site.py` | Long-running change detection daemon |
| `start_monitor.ps1` | PowerShell wrapper for `monitor_site.py` |
| `serve_mirror.py` | Local HTTP server with URL rewriting |
| `MIRROR_MANIFEST.md` | Full file listing (auto-generated) |
| `POST_ID_SERIES.md` | Post/page/media ID analysis |
| `state/monitor_state.json` | Persistent daemon state (git-friendly JSON) |
| `monitor/` | Change reports and logs from the daemon |

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
| **Fast** | 30s | Sitemap HEAD (ETag/Last-Modified) — quick change flag |
| **Medium** | 120s | All REST API collections + namespace endpoints (conditional GET + pagination) |
| **Deep** | 1800s | Page content hashes (5/cycle), static assets (theme/plugin CSS/JS/fonts), discovery docs, extras, oEmbed, media thumbnails, orphan ID probe (30 IDs/cycle) |

### What the Monitor Detects

| Change Type | How |
|-------------|-----|
| New/removed sitemap URLs | Sitemap content hash diff |
| New/removed/modified API items | Paginated collection fetch + item-level diff |
| Page content changes | Full-content hash comparison |
| Media file replacement | `source_url` change tracking |
| Orphan/unattached uploads | `post_parent == 0` detection |
| Media thumbnail variants | HEAD + ETag per `media_details.sizes` entry |
| JSON endpoint changes | Conditional GET + hash for any REST endpoint |
| Endpoint auth transitions | 401/403 → 200 status change detection |
| Static asset changes | Conditional GET on known CSS/JS/font/image files |
| Discovery doc changes | Conditional GET on robots.txt, sitemap XSL, etc. |
| Unpublished/draft items | HEAD probe of IDs beyond the maximum known ID (chunked: 30/cycle) |

### Rate-Limit Protections

- Random jitter on all sequential requests
- 429 detection with exponential backoff (global cooldown)
- Adaptive orphan probing (30 IDs per deep cycle, not all 300 at once)
- `ThreadPoolExecutor` parallelism capped at 6–8 workers

### Discord Notifications

All alerts are sent as rich embeds with color-coded categories. Each change type gets its own embed. No plain text messages. A summary embed is sent if no standard type matches.

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
