/* ── Operator callsign ────────────────────────────────────
   Shared JS for all Ops Centre pages.
   Displays the operator callsign chosen on the landing page
   (or in the Tools query bar), stored in localStorage as
   'operator'. The Cloudflare presence backend is
   decommissioned; this is local display only.
   ─────────────────────────────────────────────────────── */

(function () {
  var operatorName = '';
  var callsignEl = null;

  function getCallsign() {
    try { return (localStorage.getItem('operator') || '').trim(); }
    catch { return ''; }
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

    var buttonEl = document.createElement('span');
    buttonEl.id = 'ops-presence-btn';
    buttonEl.className = 'ops-presence-btn';
    buttonEl.innerHTML = '<span class="ops-presence-callsign">' + escHtml(operatorName || getCallsign() || 'anon') + '</span>';
    callsignEl = buttonEl.querySelector('.ops-presence-callsign');
    opDisplay.parentNode.insertBefore(buttonEl, opDisplay.nextSibling);

    if (!document.getElementById('ops-presence-style')) {
      var css = document.createElement('style');
      css.id = 'ops-presence-style';
      css.textContent = '' +
        '.ops-presence-btn { user-select: none; white-space: nowrap; font-family: \'IBM Plex Mono\', monospace; font-size: 11px; }' +
        '.ops-presence-btn .ops-presence-callsign { color: #0f0; }';
      document.head.appendChild(css);
    }
  }

  function setCallsign(name) {
    operatorName = (name || '').trim();
    if (!callsignEl) createUI();
    if (callsignEl) callsignEl.textContent = operatorName || 'anon';
  }

  /* Live callsign update (used by the /tools query bar).
     Updates the display as the user types. */
  window.OpsPresenceSetCallsign = function (name) {
    setCallsign(name);
  };

  function init() {
    var callsign = getCallsign();
    if (!callsign) { setTimeout(init, 1000); return; }
    operatorName = callsign;
    createUI();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();

/* -- Twitch status indicator ----------------------------------
   Shared TTV indicator for all Ops Centre pages.
   Reads docs/status/twitch.json and renders #twitchStatus.
   The stream tracker is decommissioned; the file reports
   state DECOMMISSIONED, rendered as a static label.
   -------------------------------------------------------------- */
(function(){
  var lastState = null;
  var twitchTick = null;
  function fmtElapsed(s) {
    var d = Math.floor(s / 86400), h = Math.floor((s % 86400) / 3600), m = Math.floor((s % 3600) / 60), sec = Math.floor(s % 60);
    if (d > 0) return d + 'd ' + h + 'h ' + m + 'm';
    if (h > 0) return h + 'h ' + m + 'm ' + sec + 's';
    if (m > 0) return m + 'm ' + sec + 's';
    return sec + 's';
  }
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
    } else if (state === 'DECOMMISSIONED') {
      el.innerHTML = '<span class="trace-dot trace-dot--lost"></span><span class="trace-label">TTV: DECOMMISSIONED</span>';
    } else {
      el.innerHTML = '';
    }
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
      if (lastState !== null && data.state !== lastState) {
        var audio = new Audio();
        audio.src = data.state === 'LIVE' ? '../data/alien_menu_notif.mp3' : '../data/alien_menu_save.mp3';
        audio.volume = 0.3;
        audio.play().catch(function(){});
      }
      renderTwitch(data);
      lastState = data.state;
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
    var d = Math.floor(s / 86400), h = Math.floor((s % 86400) / 3600), m = Math.floor((s % 3600) / 60), sec = Math.floor(s % 60);
    if (d > 0) return d + 'd ' + h + 'h ' + m + 'm';
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
      if (lastTraceState !== null && data.state !== lastTraceState) {
        var audio = new Audio();
        audio.src = data.state === 'ACTIVE' ? '../data/alien_mt_notif.mp3' : '../data/alien_mt_power.mp3';
        audio.volume = 0.3;
        audio.play().catch(function(){});
      }
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
