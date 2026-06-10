/* ── CONFIG ────────────────────────────────────────────── */
const DATA_ROOT = '../data'; /* relative to docs/status/ */
const SITE_LIVE  = 'https://project-skyscraper.com';

/* ── STATE ─────────────────────────────────────────────── */
let feed     = [];    /* changes feed, newest-first */
let manifest = [];    /* all known pages/posts */
let external = [];    /* external factors feed */
let avatarUrl = '../favicon.jpg';

/* ── HELPERS ───────────────────────────────────────────── */
function setOperator() {
  const el = document.getElementById('operatorDisplay');
  if (!el) return;
  const name = localStorage.getItem('operator') || '';
  el.textContent = name ? `Operator: ${name}` : 'Operator: <anon>';
}
function fmtBoth(isoString) {
  if (!isoString) return '';
  const d = new Date(isoString);
  const opts = { day: '2-digit', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit', second: '2-digit' };
  const utc   = d.toLocaleString('en-GB', { ...opts, timeZone: 'UTC' });
  const local = d.toLocaleString('en-GB', opts);
  return `<span class="ts-stack"><span class="ts-utc">${utc} UTC</span><span class="ts-local">${local} local</span></span>`;
}

function esc(s) {
  const e = document.createElement('div');
  e.textContent = s;
  return e.innerHTML;
}

function renderDiff(raw) {
  if (!raw) return '';
  return raw.split('\n').map(line => {
    if (!line) return '';
    if (line.startsWith('+ ')) return `<span class="diff-add">${esc(line)}</span>`;
    if (line.startsWith('- ')) return `<span class="diff-rem">${esc(line)}</span>`;
    if (line.startsWith('@@')) return `<span class="diff-hunk">${esc(line)}</span>`;
    if (line.startsWith('...')) return `<span class="diff-more">${esc(line)}</span>`;
    return `<span class="diff-ctx">${esc(line)}</span>`;
  }).join('\n');
}

/* ── RENDER FEED ───────────────────────────────────────── */
function renderFeed(entries) {
  const container = document.getElementById('feedEntries');
  if (!entries.length) {
    container.innerHTML = '<div class="empty-msg">no changes recorded yet</div>';
    return;
  }
  container.innerHTML = entries.map(e => {
    const icon = e.type === 'added' || e.type.endsWith('_added') ? '+' : e.type === 'removed' || e.type.endsWith('_removed') ? '−' : '~';
    const tagCls = `tag tag-${e.type}`;
    return `
      <div class="card">
        <img src="${esc(avatarUrl)}" alt="" class="card-avatar" loading="lazy">
        <div class="card-body">
          ${e.link
            ? `<a href="${esc(e.link)}" target="_blank" rel="noopener" class="card-title">${esc(e.title || e.detail || 'untitled')}</a>`
            : `<span class="card-title card-title--no-link">${esc(e.title || e.detail || 'untitled')}</span>`}
          <div class="card-meta">
            <span class="${tagCls}">${icon} ${e.type}</span>
            ${fmtBoth(e.timestamp)}
            ${e.endpoint ? `<span>${esc(e.endpoint.split('/').pop())}</span>` : ''}
            ${e.author ? `<span>by ${esc(e.author)}</span>` : ''}
            ${e.diff ? `<span class="diff-toggle" data-idx="${e._idx}">&#9654; diff</span>` : ''}
          </div>
          ${e.diff ? `<div class="card-diff hidden" id="diff-${e._idx}"><pre class="diff-block">${renderDiff(e.diff)}</pre></div>` : ''}
        </div>
      </div>`;
  }).join('');

  /* Diff toggle */
  container.querySelectorAll('.diff-toggle').forEach(function(btn) {
    btn.addEventListener('click', function() {
      var id = btn.dataset.idx;
      var diffEl = document.getElementById('diff-' + id);
      if (diffEl) {
        diffEl.classList.toggle('hidden');
        btn.innerHTML = diffEl.classList.contains('hidden') ? '\u25B4 diff' : '\u25BE diff';
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
            <span class="card-meta-label">modified</span>${fmtBoth(m.modified)}
            <span class="card-meta-label">created</span>${fmtBoth(m.date_gmt)}
            <span>${esc(m.path)}</span>
            ${m.author ? `<span>by ${esc(m.author)}</span>` : ''}
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
    (m.path && m.path.toLowerCase().includes(q)) ||
    (m.author && m.author.toLowerCase().includes(q))
  );
  renderManifest(filtered);
}

/* ── RENDER EXTERNAL ───────────────────────────────────── */
function renderExternal(entries) {
  const container = document.getElementById('externalEntries');
  if (!entries.length) {
    container.innerHTML = '<div class="empty-msg">no external events recorded yet</div>';
    return;
  }
  container.innerHTML = entries.map(e => {
    const tagCls = `tag tag-${e.type}`;
    return `
      <div class="card">
        <img src="${esc(avatarUrl)}" alt="" class="card-avatar" loading="lazy">
        <div class="card-body">
          ${e.link
            ? `<a href="${esc(e.link)}" target="_blank" rel="noopener" class="card-title">${esc(e.title || e.detail || 'untitled')}</a>`
            : `<span class="card-title card-title--no-link">${esc(e.title || e.detail || 'untitled')}</span>`}
          <div class="card-meta">
            <span class="${tagCls}">${e.type.replace('external_', 'ext:')}</span>
            ${e.site ? `<span class="tag tag-site">${esc(e.site)}</span>` : ''}
            ${fmtBoth(e.timestamp)}
            ${e.detail ? `<span>${esc(e.detail.substring(0, 120))}</span>` : ''}
            ${e.diff ? `<span class="diff-toggle" data-idx="${e._idx}">&#9654; diff</span>` : ''}
          </div>
          ${e.diff ? `<div class="card-diff hidden" id="diff-ext-${e._idx}"><pre class="diff-block">${renderDiff(e.diff)}</pre></div>` : ''}
        </div>
      </div>`;
  }).join('');

  container.querySelectorAll('.diff-toggle').forEach(function(btn) {
    btn.addEventListener('click', function() {
      var id = btn.dataset.idx;
      var diffEl = document.getElementById('diff-ext-' + id);
      if (diffEl) {
        diffEl.classList.toggle('hidden');
        btn.innerHTML = diffEl.classList.contains('hidden') ? '\u25B4 diff' : '\u25BE diff';
      }
    });
  });
}

/* ── FILTER EXTERNAL ───────────────────────────────────── */
function filterExternal() {
  const q   = document.getElementById('externalSearch').value.toLowerCase();
  const typ = document.getElementById('externalFilter').value;
  let filtered = external;
  if (typ !== 'all') {
    if (typ === 'wakingtitan' || typ === 'tower') {
      filtered = filtered.filter(e => e.site === typ);
    } else {
      filtered = filtered.filter(e => e.type.includes(typ));
    }
  }
  if (q) filtered = filtered.filter(e =>
    (e.title && e.title.toLowerCase().includes(q)) ||
    (e.detail && e.detail.toLowerCase().includes(q)) ||
    (e.type && e.type.toLowerCase().includes(q)) ||
    (e.site && e.site.toLowerCase().includes(q))
  );
  renderExternal(filtered);
}

/* ── LOAD DATA ─────────────────────────────────────────── */
async function load() {
  try {
    const [feedResp, manifestResp, externalResp] = await Promise.all([
      fetch(`${DATA_ROOT}/feed.json`),
      fetch(`${DATA_ROOT}/manifest.json`),
      fetch(`${DATA_ROOT}/external.json`),
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
    if (externalResp.ok) {
      const raw = await externalResp.json();
      external = (raw.entries || []).map((e, i) => ({ ...e, _idx: i }));
      external.sort((a, b) => new Date(b.timestamp) - new Date(a.timestamp));
      renderExternal(external);
    }
    if (feedResp.ok || manifestResp.ok || externalResp.ok) {
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
document.getElementById('externalSearch').addEventListener('input', filterExternal);
document.getElementById('externalFilter').addEventListener('change', filterExternal);

/* Tab switching */
document.querySelectorAll('.tab').forEach(btn => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
    btn.classList.add('active');
    document.querySelectorAll('#feed, #manifest, #external').forEach(s => s.classList.remove('active'));
    const target = document.getElementById(btn.dataset.tab);
    if (target) target.classList.add('active');
  });
});

load();
setOperator();

/* ── TRACE (Discourse online status) ───────────────────── */
let traceTick = null;

function fmtElapsed(seconds) {
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = Math.floor(seconds % 60);
  if (h > 0) return `${h}h${String(m).padStart(2,'0')}m${String(s).padStart(2,'0')}s`;
  if (m > 0) return `${m}m${String(s).padStart(2,'0')}s`;
  return `${s}s`;
}

function renderTrace(data) {
  const el = document.getElementById('traceStatus');
  if (!el) return;

  if (data.state === 'ACTIVE') {
    el.innerHTML = `<span class="trace-dot trace-dot--active"></span><span class="trace-label">TRACE: ACTIVE</span>`;
  } else if (data.state === 'LOST' && data.lastSeenAt) {
    const then = new Date(data.lastSeenAt);
    const elapsed = (Date.now() - then.getTime()) / 1000;
    el.innerHTML = `<span class="trace-dot trace-dot--lost"></span><span class="trace-label">TRACE: LOST</span> <span class="trace-time">-${fmtElapsed(elapsed)}</span>`;
  } else {
    el.innerHTML = '';
  }
}

function updateTrace() {
  fetch('trace.json')
    .then(r => r.ok ? r.json() : Promise.reject(r.status))
    .then(data => {
      renderTrace(data);
      if (data.state === 'LOST') {
        if (traceTick) clearInterval(traceTick);
        traceTick = setInterval(() => renderTrace(data), 1000);
      } else {
        if (traceTick) { clearInterval(traceTick); traceTick = null; }
      }
    })
    .catch(() => {
      const el = document.getElementById('traceStatus');
      if (el) el.innerHTML = '';
    });
}

updateTrace();
// Re-fetch trace.json every 30s so ACTIVE→LOST flip isn't missed (silent while ticking)
setInterval(updateTrace, 30000);
