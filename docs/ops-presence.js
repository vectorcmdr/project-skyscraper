/* ── Operator Presence ────────────────────────────────────
   Shared JS for all Ops Centre pages.
   Shows online operator count + overlay list.
   Click your callsign to toggle online/offline.
   Reads operator callsign from localStorage (set by each page).
   ─────────────────────────────────────────────────────── */

(function () {
  const WORKER_URL = 'https://opscentre.josh-axey-3006.workers.dev/presence';
  const HEARTBEAT_INTERVAL = 45000;
  const POLL_INTERVAL = 15000;
  const STALE_AFTER_MS = 40000;

  let operatorName = '';
  let isOnline = true;
  let operators = [];
  let buttonEl = null;
  let countEl = null;
  let overlayEl = null;
  let listEl = null;
  let callsignEl = null;
  let dotEl = null;
  let heartbeatTimer = null;
  let pollTimer = null;

  function getCallsign() {
    try { return (localStorage.getItem('operator') || '').trim(); }
    catch { return ''; }
  }

  function getOnlinePref() {
    try {
      var v = localStorage.getItem('ops_online');
      return v === null ? true : v === 'true';
    } catch { return true; }
  }

  function setOnlinePref(val) {
    try { localStorage.setItem('ops_online', val ? 'true' : 'false'); } catch {}
  }

  function sendHeartbeat() {
    if (!isOnline) return;
    var name = getCallsign();
    if (!name) return;
    operatorName = name;
    fetch(WORKER_URL, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ callsign: name }),
    }).catch(function () {});
  }

  function goOffline() {
    isOnline = false;
    setOnlinePref(false);
    if (heartbeatTimer) { clearInterval(heartbeatTimer); heartbeatTimer = null; }
    var name = getCallsign();
    if (name) {
      fetch(WORKER_URL, { method: 'DELETE', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ callsign: name }) })
        .catch(function () {});
    }
    if (callsignEl) callsignEl.textContent = (operatorName || getCallsign() || 'anon') + ' (offline)';
    if (dotEl) dotEl.style.background = '#555';
  }

  function goOnline() {
    isOnline = true;
    setOnlinePref(true);
    if (callsignEl) callsignEl.textContent = operatorName || getCallsign() || 'anon';
    if (dotEl) dotEl.style.background = '';
    sendHeartbeat();
    if (!heartbeatTimer) heartbeatTimer = setInterval(sendHeartbeat, HEARTBEAT_INTERVAL);
  }

  function toggleOnline() {
    if (isOnline) goOffline(); else goOnline();
  }

  function fetchOperators() {
    fetch(WORKER_URL)
      .then(function (r) { return r.ok ? r.json() : Promise.reject(); })
      .then(function (data) {
        if (!data || !data.operators) return;
        var now = Date.now();
        operators = data.operators.filter(function (o) { return now - o.lastSeen < STALE_AFTER_MS; });
        updateUI();
      })
      .catch(function () {});
  }

  function updateUI() {
    var active = operators;
    var count = active.filter(function (o) { return o.callsign !== operatorName; }).length;
    if (countEl) { countEl.textContent = isOnline ? count : '\u2014'; }
    if (buttonEl) buttonEl.classList.toggle('has-online', isOnline && count > 0);
    if (!listEl) return;
    if (!isOnline) {
      listEl.innerHTML = '<div class="ops-presence-empty">You are offline. Click your callsign to go online.</div>';
      return;
    }
    if (active.length === 0) {
      listEl.innerHTML = '<div class="ops-presence-empty">No operators online</div>';
      return;
    }
    listEl.innerHTML = active.map(function (op) {
      var isMe = op.callsign === operatorName;
      var ago = Date.now() - op.lastSeen;
      var label = ago < 5000 ? 'active now' : ago < 30000 ? Math.floor(ago / 1000) + 's ago' : 'seen recently';
      return '<div class="ops-presence-item' + (isMe ? ' ops-presence-item--self' : '') + '">' +
        '<span class="ops-presence-dot"></span>' +
        '<span class="ops-presence-callsign">' + escHtml(op.callsign) + '</span>' +
        '<span class="ops-presence-status">' + label + '</span></div>';
    }).join('');
  }

  function escHtml(s) {
    var e = document.createElement('div');
    e.textContent = s;
    return e.innerHTML;
  }

  function createUI() {
    if (document.getElementById('ops-presence-btn')) return;
    var opDisplay = document.getElementById('operatorDisplay');
    if (!opDisplay) return;
    opDisplay.style.display = 'none';

    isOnline = getOnlinePref();
    var displayName = operatorName || getCallsign() || 'anon';

    buttonEl = document.createElement('span');
    buttonEl.id = 'ops-presence-btn';
    buttonEl.className = 'ops-presence-btn';
    buttonEl.innerHTML = '<span class="ops-presence-callsign">' + escHtml(displayName) + '</span> <span class="ops-presence-sep">|</span> <span class="ops-presence-dot"></span> <span class="ops-presence-count">0</span> online <span class="ops-presence-arrow">&#9660;</span>';
    callsignEl = buttonEl.querySelector('.ops-presence-callsign');
    countEl = buttonEl.querySelector('.ops-presence-count');
    dotEl = buttonEl.querySelector('.ops-presence-dot');
    opDisplay.parentNode.insertBefore(buttonEl, opDisplay.nextSibling);

    if (!isOnline) {
      callsignEl.textContent = displayName + ' (offline)';
      dotEl.style.background = '#555';
      countEl.textContent = '\u2014';
    }

    // Click callsign to toggle online/offline
    callsignEl.addEventListener('click', function (e) {
      e.stopPropagation();
      toggleOnline();
    });
    callsignEl.title = 'Click to toggle online/offline';

    // Click rest of button to toggle overlay
    var restOfButton = function (e) {
      if (e.target === callsignEl) return;
      e.stopPropagation();
      e.preventDefault();
      var isOpen = overlayEl.classList.contains('visible');
      document.querySelectorAll('.ops-presence-overlay').forEach(function (o) { o.classList.remove('visible'); });
      if (isOpen) {
        overlayEl.classList.remove('visible');
        buttonEl.querySelector('.ops-presence-arrow').innerHTML = '&#9660;';
      } else {
        overlayEl.classList.add('visible');
        buttonEl.querySelector('.ops-presence-arrow').innerHTML = '&#9652;';
        if (isOnline) fetchOperators();
      }
    };
    buttonEl.addEventListener('click', restOfButton);

    overlayEl = document.createElement('div');
    overlayEl.id = 'ops-presence-overlay';
    overlayEl.className = 'ops-presence-overlay';
    overlayEl.innerHTML = '<div class="ops-presence-header">OPERATORS ONLINE</div><div class="ops-presence-list"></div>';
    listEl = overlayEl.querySelector('.ops-presence-list');

    document.addEventListener('click', function (e) {
      if (!e.target.closest('#ops-presence-btn') && !e.target.closest('#ops-presence-overlay')) {
        overlayEl.classList.remove('visible');
        var a = buttonEl && buttonEl.querySelector('.ops-presence-arrow');
        if (a) a.innerHTML = '&#9660;';
      }
    });
    overlayEl.addEventListener('click', function (e) { e.stopPropagation(); });
    document.body.appendChild(overlayEl);

    if (!document.getElementById('ops-presence-style')) {
      var css = document.createElement('style');
      css.id = 'ops-presence-style';
      css.textContent = '' +
        '.ops-presence-btn { cursor: pointer; user-select: none; white-space: nowrap; font-family: \'IBM Plex Mono\', monospace; font-size: 11px; color: #ccc; transition: color 0.2s; }' +
        '.ops-presence-btn:hover { color: #0f0; }' +
        '.ops-presence-btn .ops-presence-callsign { color: #0f0; cursor: pointer; border-bottom: 1px dotted #444; }' +
        '.ops-presence-btn .ops-presence-callsign:hover { border-bottom-color: #0f0; }' +
        '.ops-presence-btn .ops-presence-dot { width: 7px; height: 7px; border-radius: 50%; background: #555; display: inline-block; vertical-align: middle; transition: background 0.3s; }' +
        '.ops-presence-btn.has-online .ops-presence-dot { background: #0f0; }' +
        '.ops-presence-sep { color: #333; margin: 0 4px; }' +
        '.ops-presence-count { font-weight: bold; }' +
        '.ops-presence-arrow { font-size: 8px; margin-left: 2px; display: inline-block; }' +
        '.ops-presence-overlay { position: fixed; top: 55px; right: 20px; z-index: 9998; background: #111; border: 1px solid #333; padding: 12px; min-width: 220px; max-height: 320px; overflow-y: auto; font-family: \'IBM Plex Mono\', monospace; font-size: 12px; display: none; box-shadow: 0 4px 20px rgba(0,0,0,0.6); }' +
        '.ops-presence-overlay.visible { display: block; }' +
        '.ops-presence-header { color: #888; text-transform: uppercase; font-size: 10px; letter-spacing: 1px; margin-bottom: 8px; padding-bottom: 6px; border-bottom: 1px solid #333; }' +
        '.ops-presence-empty { color: #555; font-style: italic; text-align: center; padding: 12px 0; }' +
        '.ops-presence-item { display: flex; align-items: center; gap: 8px; padding: 4px 0; color: #ccc; }' +
        '.ops-presence-item--self { color: #0f0; }' +
        '.ops-presence-item .ops-presence-dot { width: 6px; height: 6px; border-radius: 50%; background: #0f0; flex-shrink: 0; }' +
        '.ops-presence-callsign { flex: 1; }' +
        '.ops-presence-status { color: #666; font-size: 11px; }';
      document.head.appendChild(css);
    }
  }

  function init() {
    var callsign = getCallsign();
    if (!callsign) { setTimeout(init, 1000); return; }
    operatorName = callsign;
    createUI();
    if (isOnline) {
      sendHeartbeat();
      heartbeatTimer = setInterval(sendHeartbeat, HEARTBEAT_INTERVAL);
    }
    fetchOperators();
    pollTimer = setInterval(fetchOperators, POLL_INTERVAL);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();

/* ── Twitch live/offline status ───────────────────────────
   Shared TTV indicator for all Ops Centre pages.
   Reads docs/status/twitch.json and renders #twitchStatus.
   ─────────────────────────────────────────────────────── */
(function(){
  var lastState = null;
  var twitchTick = null;
  function renderTwitch(data) {
    var el = document.getElementById('twitchStatus');
    if (!el) return;
    var state = data.state;
    if (state === 'LIVE') {
      el.innerHTML = '<span class="trace-dot" style="background:#9146ff;box-shadow:0 0 6px #9146ff;animation:trace-pulse 1.5s ease-in-out infinite"></span><span class="trace-label" style="color:#9146ff">TTV: LIVE</span>';
    } else if (state === 'OFFLINE') {
      var since = new Date(data.updatedAt);
      var elapsed = (Date.now() - since.getTime()) / 1000;
      el.innerHTML = '<span class="trace-dot trace-dot--lost"></span><span class="trace-label">TTV: OFFLINE</span> <span class="trace-time">-' + fmtElapsed(elapsed) + '</span>';
    } else {
      el.innerHTML = '';
    }
    lastState = state;
  }
  function scheduleTwitchTick(data) {
    if (twitchTick) { clearInterval(twitchTick); twitchTick = null; }
    if (data.state === 'OFFLINE') {
      twitchTick = setInterval(function() { renderTwitch(data); }, 1000);
    }
  }
  function fetchTwitch() {
    fetch('../status/twitch.json').then(function(r) {
      if (!r.ok) throw new Error();
      return r.json();
    }).then(function(data) {
      renderTwitch(data);
      scheduleTwitchTick(data);
    }).catch(function() {
      var el = document.getElementById('twitchStatus');
      if (el) el.innerHTML = '';
    });
  }
  function initTwitch() {
    if (document.getElementById('twitchStatus')) {
      fetchTwitch();
      setInterval(fetchTwitch, 60000);
    }
  }
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initTwitch);
  } else {
    initTwitch();
  }
})();

/* ── TRACE (The Architect online status) ──────────────────
   Shared across all Ops Centre pages.
   Reads docs/status/trace.json and renders #traceStatus.
   ─────────────────────────────────────────────────────── */
(function(){
  var traceTick = null;
  var lastTraceState = null;
  function fmtElapsed(s) {
    var h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = Math.floor(s % 60);
    if (h > 0) return h + 'h ' + m + 'm ' + sec + 's';
    if (m > 0) return m + 'm ' + sec + 's';
    return sec + 's';
  }
  function renderTrace(data) {
    var el = document.getElementById('traceStatus');
    if (!el) return;
    if (data.state === 'ACTIVE') {
      el.innerHTML = '<span class="trace-dot trace-dot--active"></span><span class="trace-label">TRACE: ACTIVE</span>';
    } else if (data.state === 'LOST' && data.lastSeenAt) {
      var then = new Date(data.lastSeenAt);
      var elapsed = (Date.now() - then.getTime()) / 1000;
      el.innerHTML = '<span class="trace-dot trace-dot--lost"></span><span class="trace-label">TRACE: LOST</span> <span class="trace-time">-' + fmtElapsed(elapsed) + '</span>';
    } else {
      el.innerHTML = '';
    }
  }
  function updateTrace() {
    fetch('../status/trace.json').then(function(r) {
      if (!r.ok) throw new Error();
      return r.json();
    }).then(function(data) {
      renderTrace(data);
      lastTraceState = data.state;
      if (data.state === 'LOST') {
        if (traceTick) clearInterval(traceTick);
        traceTick = setInterval(function() { renderTrace(data); }, 1000);
      } else {
        if (traceTick) { clearInterval(traceTick); traceTick = null; }
      }
    }).catch(function() {
      var el = document.getElementById('traceStatus');
      if (el) el.innerHTML = '';
    });
  }
  function initTrace() {
    if (document.getElementById('traceStatus')) {
      updateTrace();
      setInterval(updateTrace, 30000);
    }
  }
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initTrace);
  } else {
    initTrace();
  }
})();
