/* ── CONFIG ────────────────────────────────────────────── */
const DATA_ROOT = '../data'; /* relative to site/status/ */
const SITE_LIVE  = 'https://project-skyscraper.com';

/* ── STATE ─────────────────────────────────────────────── */
let feed     = [];    /* changes feed, newest-first */
let manifest = [];    /* all known pages/posts */
let avatarUrl = '../favicon.jpg';

/* ── HELPERS ───────────────────────────────────────────── */
function ago(isoString) {
  if (!isoString) return '';
  const d = new Date(isoString.endsWith('Z') ? isoString : isoString + 'Z');
  const now = new Date();
  const sec = Math.floor((now - d) / 1000);
  if (sec < 60)   return `${sec}s ago`;
  const min = Math.floor(sec / 60);
  if (min < 60)   return `${min}m ago`;
  const hr  = Math.floor(min / 60);
  if (hr < 24)   return `${hr}h ago`;
  const days = Math.floor(hr / 24);
  return `${days}d ago`;
}

function fmtDate(isoString) {
  if (!isoString) return '—';
  const d = new Date(isoString.endsWith('Z') ? isoString : isoString + 'Z');
  return d.toLocaleString('en-GB', { day: '2-digit', month: 'short', year: 'numeric',
    hour: '2-digit', minute: '2-digit' });
}

function esc(s) {
  const e = document.createElement('div');
  e.textContent = s;
  return e.innerHTML;
}

/* ── RENDER FEED ───────────────────────────────────────── */
function renderFeed(entries) {
  const el = document.getElementById('feedEntries');
  if (!entries.length) {
    el.innerHTML = '<div class="empty-msg">no changes recorded yet</div>';
    return;
  }
  el.innerHTML = entries.map(e => {
    const icon = e.type === 'added' || e.type.endsWith('_added') ? '+' : e.type === 'removed' || e.type.endsWith('_removed') ? '−' : '~';
    const tagCls = `tag tag-${e.type}`;
    return `
      <div class="card">
        <img src="${esc(avatarUrl)}" alt="" class="card-avatar" loading="lazy">
        <div class="card-body">
          <a href="${esc(e.link)}" target="_blank" rel="noopener" class="card-title">${esc(e.title || e.detail || 'untitled')}</a>
          <div class="card-meta">
            <span class="${tagCls}">${icon} ${e.type}</span>
            <span>${ago(e.timestamp)}</span>
            ${e.endpoint ? `<span>${esc(e.endpoint.split('/').pop())}</span>` : ''}
            ${e.diff ? `<span class="diff-toggle" data-idx="${e._idx}">&#9654; diff</span>` : ''}
          </div>
          ${e.diff ? `<div class="card-diff hidden" id="diff-${e._idx}">${esc(e.diff)}</div>` : ''}
        </div>
      </div>`;
  }).join('');

  /* Diff toggle */
  document.querySelectorAll('.diff-toggle').forEach(el => {
    el.addEventListener('click', () => {
      const id = el.dataset.idx;
      const diffEl = document.getElementById('diff-' + id);
      if (diffEl) {
        diffEl.classList.toggle('hidden');
        el.innerHTML = diffEl.classList.contains('hidden') ? '&#9654; diff' : '&#9660; diff';
      }
    });
  });
}

/* ── RENDER MANIFEST ───────────────────────────────────── */
function renderManifest(entries) {
  const el = document.getElementById('manifestEntries');
  if (!entries.length) {
    el.innerHTML = '<div class="empty-msg">no pages tracked yet</div>';
    return;
  }
  el.innerHTML = entries.map(m => {
    const tagCls = `tag tag-${m.type}`;
    return `
      <div class="card manifest-item">
        <img src="${esc(avatarUrl)}" alt="" class="card-avatar" loading="lazy">
        <div class="card-body">
          <a href="${SITE_LIVE}${esc(m.path)}" target="_blank" rel="noopener" class="card-title">${esc(m.title || m.path)}</a>
          <div class="card-meta">
            <span class="${tagCls}">${esc(m.type)}</span>
            <span>modified ${ago(m.modified)}</span>
            <span>created ${fmtDate(m.date_gmt)}</span>
            <span>${esc(m.path)}</span>
          </div>
        </div>
      </div>`;
  }).join('');
}

/* ── FILTER MANIFEST ───────────────────────────────────── */
function filterManifest() {
  const q   = document.getElementById('manifestSearch').value.toLowerCase();
  const typ = document.getElementById('manifestFilter').value;
  let filtered = manifest;
  if (typ !== 'all') filtered = filtered.filter(m => m.type === typ);
  if (q) filtered = filtered.filter(m =>
    (m.title && m.title.toLowerCase().includes(q)) ||
    (m.path && m.path.toLowerCase().includes(q))
  );
  renderManifest(filtered);
}

/* ── LOAD DATA ─────────────────────────────────────────── */
async function load() {
  try {
    const [feedResp, manifestResp] = await Promise.all([
      fetch(`${DATA_ROOT}/feed.json`),
      fetch(`${DATA_ROOT}/manifest.json`),
    ]);
    if (feedResp.ok) {
      const raw = await feedResp.json();
      feed = (raw.entries || []).map((e, i) => ({ ...e, _idx: i }));
      feed.sort((a, b) => new Date(b.timestamp) - new Date(a.timestamp));
      renderFeed(feed);
    }
    if (manifestResp.ok) {
      manifest = (await manifestResp.json()).pages || [];
      manifest.sort((a, b) => new Date(b.modified) - new Date(a.modified));
      renderManifest(manifest);
    }
    if (feedResp.ok || manifestResp.ok) {
      document.getElementById('statusBadge').textContent = '\u25CF ONLINE';
      document.getElementById('statusBadge').className = 'topbar-status status-online';
    }
  } catch (err) {
    console.error('Failed to load data:', err);
    document.getElementById('statusBadge').textContent = '\u25CF OFFLINE';
  }
}

/* ── EVENTS ────────────────────────────────────────────── */
document.getElementById('manifestSearch').addEventListener('input', filterManifest);
document.getElementById('manifestFilter').addEventListener('change', filterManifest);

load();
