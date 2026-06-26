/* ── Operator Presence ────────────────────────────────────
   Shared JS for all Ops Centre pages.
   Shows online operator count + overlay list.
   Requires Cloudflare Worker URL set below.
   Reads operator callsign from localStorage (set by each page).
   ─────────────────────────────────────────────────────── */

(function () {
  const WORKER_URL = 'https://opscentre.josh-axey-3006.workers.dev/presence';
  const HEARTBEAT_INTERVAL = 30000;  // 30s
  const POLL_INTERVAL = 15000;       // 15s
  const STALE_AFTER_MS = 40000;      // consider operator offline after 40s

  let operatorName = '';
  let operators = [];
  let buttonEl = null;
  let countEl = null;
  let overlayEl = null;
  let listEl = null;
  let heartbeatTimer = null;
  let pollTimer = null;

  /* ── Read callsign from localStorage ─────────────────── */
  function getCallsign() {
    try {
      return (localStorage.getItem('operator') || '').trim();
    } catch { return ''; }
  }

  /* ── POST heartbeat ──────────────────────────────────── */
  function sendHeartbeat() {
    const name = getCallsign();
    if (!name) return;
    operatorName = name;
    fetch(WORKER_URL, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ callsign: name }),
    }).catch(() => { /* silently ignore */ });
  }

  /* ── GET operator list ───────────────────────────────── */
  function fetchOperators() {
    fetch(WORKER_URL)
      .then(r => r.ok ? r.json() : Promise.reject())
      .then(data => {
        if (!data || !data.operators) return;
        const now = Date.now();
        operators = data.operators.filter(o => now - o.lastSeen < STALE_AFTER_MS);
        updateUI();
      })
      .catch(() => {});
  }

  /* ── Update UI ───────────────────────────────────────── */
  function updateUI() {
    const active = operators;
    // Count excludes yourself — shows how many OTHER operators are online
    const count = active.filter(function(o) { return o.callsign !== operatorName; }).length;

    if (countEl) {
      countEl.textContent = count;
      buttonEl.classList.toggle('has-online', count > 0);
    }

    if (!listEl) return;
    if (active.length === 0) {
      listEl.innerHTML = '<div class="ops-presence-empty">No operators online</div>';
      return;
    }

    listEl.innerHTML = active.map(op => {
      const isMe = op.callsign === operatorName;
      const ago = Date.now() - op.lastSeen;
      const label = ago < 5000 ? 'active now'
                  : ago < 30000 ? `${Math.floor(ago / 1000)}s ago`
                  : 'seen recently';
      return `<div class="ops-presence-item${isMe ? ' ops-presence-item--self' : ''}">
        <span class="ops-presence-dot"></span>
        <span class="ops-presence-callsign">${escHtml(op.callsign)}</span>
        <span class="ops-presence-status">${label}</span>
      </div>`;
    }).join('');
  }

  function escHtml(s) {
    const e = document.createElement('div');
    e.textContent = s;
    return e.innerHTML;
  }

  /* ── Create UI elements ──────────────────────────────── */
  function createUI() {
    if (document.getElementById('ops-presence-btn')) return;

    const opDisplay = document.getElementById('operatorDisplay');
    if (!opDisplay) return;

    // Hide old operator display — the presence button replaces it
    opDisplay.style.display = 'none';

    // Build presence button: "{callsign} | ● N Online ▼"
    const displayName = operatorName || getCallsign() || 'anon';

    buttonEl = document.createElement('span');
    buttonEl.id = 'ops-presence-btn';
    buttonEl.className = 'ops-presence-btn';
    buttonEl.innerHTML = `<span class="ops-presence-callsign">${escHtml(displayName)}</span> <span class="ops-presence-sep">|</span> <span class="ops-presence-dot"></span> <span class="ops-presence-count">0</span> online <span class="ops-presence-arrow">&#9660;</span>`;
    countEl = buttonEl.querySelector('.ops-presence-count');
    opDisplay.parentNode.insertBefore(buttonEl, opDisplay.nextSibling);

    overlayEl = document.createElement('div');
    overlayEl.id = 'ops-presence-overlay';
    overlayEl.className = 'ops-presence-overlay';
    overlayEl.innerHTML = '<div class="ops-presence-header">OPERATORS ONLINE</div><div class="ops-presence-list"></div>';
    listEl = overlayEl.querySelector('.ops-presence-list');

    buttonEl.addEventListener('click', function (e) {
      e.stopPropagation();
      e.preventDefault();
      const isOpen = overlayEl.classList.contains('visible');
      document.querySelectorAll('.ops-presence-overlay').forEach(function (o) { o.classList.remove('visible'); });
      if (isOpen) {
        overlayEl.classList.remove('visible');
        buttonEl.querySelector('.ops-presence-arrow').innerHTML = '&#9660;';
      } else {
        overlayEl.classList.add('visible');
        buttonEl.querySelector('.ops-presence-arrow').innerHTML = '&#9652;';
        fetchOperators();
      }
    });

    document.addEventListener('click', function (e) {
      if (!e.target.closest('#ops-presence-btn') && !e.target.closest('#ops-presence-overlay')) {
        overlayEl.classList.remove('visible');
        var a = buttonEl && buttonEl.querySelector('.ops-presence-arrow');
        if (a) a.innerHTML = '&#9660;';
      }
    });
    overlayEl.addEventListener('click', function (e) { e.stopPropagation(); });

    document.body.appendChild(overlayEl);

    /* ── Inject CSS once ───────────────────────────────── */
    if (!document.getElementById('ops-presence-style')) {
      const css = document.createElement('style');
      css.id = 'ops-presence-style';
      css.textContent = `
        .ops-presence-btn {
          cursor: pointer; user-select: none; white-space: nowrap;
          font-family: 'IBM Plex Mono', monospace; font-size: 11px;
          color: #ccc; transition: color 0.2s;
        }
        .ops-presence-btn:hover { color: #0f0; }
        .ops-presence-btn .ops-presence-callsign { color: #0f0; }
        .ops-presence-btn .ops-presence-dot {
          width: 7px; height: 7px; border-radius: 50%;
          background: #555; display: inline-block; vertical-align: middle;
          transition: background 0.3s;
        }
        .ops-presence-btn.has-online .ops-presence-dot { background: #0f0; }
        .ops-presence-sep { color: #333; margin: 0 4px; }
        .ops-presence-count { font-weight: bold; }
        .ops-presence-arrow { font-size: 8px; margin-left: 2px; display: inline-block; }
        .ops-presence-overlay {
          position: fixed; top: 55px; right: 20px; z-index: 9998;
          background: #111; border: 1px solid #333;
          border-radius: 8px; padding: 12px; min-width: 220px;
          max-height: 320px; overflow-y: auto;
          font-family: 'IBM Plex Mono', monospace; font-size: 12px;
          display: none; box-shadow: 0 4px 20px rgba(0,0,0,0.6);
        }
        .ops-presence-overlay.visible { display: block; }
        .ops-presence-header {
          color: #888; text-transform: uppercase; font-size: 10px;
          letter-spacing: 1px; margin-bottom: 8px; padding-bottom: 6px;
          border-bottom: 1px solid #222;
        }
        .ops-presence-empty { color: #555; font-style: italic; text-align: center; padding: 12px 0; }
        .ops-presence-item {
          display: flex; align-items: center; gap: 8px;
          padding: 4px 0; color: #ccc;
        }
        .ops-presence-item--self { color: #0f0; }
        .ops-presence-item .ops-presence-dot {
          width: 6px; height: 6px; border-radius: 50%;
          background: #0f0; flex-shrink: 0;
        }
        .ops-presence-callsign { flex: 1; }
        .ops-presence-status { color: #666; font-size: 11px; }
      `;
      document.head.appendChild(css);
    }
  }

  /* ── Start ───────────────────────────────────────────── */
  function init() {
    const callsign = getCallsign();
    if (!callsign) {
      setTimeout(init, 1000);
      return;
    }
    createUI();
    // Fire heartbeat first, then fetch the list once it's registered
    sendHeartbeat();
    setTimeout(fetchOperators, 2000);
    heartbeatTimer = setInterval(sendHeartbeat, HEARTBEAT_INTERVAL);
    pollTimer = setInterval(fetchOperators, POLL_INTERVAL);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
