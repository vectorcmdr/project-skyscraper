/* ── Test Results Viewer ────────────────────────────────── */
(function () {
  var WORKER_URL = 'https://opscentre.josh-axey-3006.workers.dev';
  var allResponses = [];

  var searchEl = document.getElementById('searchInput');
  var sortEl = document.getElementById('sortSelect');
  var countEl = document.getElementById('resultsCount');
  var container = document.getElementById('resultsContainer');
  var exportBtn = document.getElementById('exportBtn');

  /* ── Operator display ────────────────────────────────── */
  function setOperator() {
    var el = document.getElementById('operatorDisplay');
    if (!el) return;
    var name = (localStorage.getItem('operator') || '').trim();
    el.textContent = name ? 'Operator: ' + name : 'Operator: <anon>';
  }

  /* ── Trace ────────────────────────────────────────────── */
  var traceTick = null;

  function fmtElapsed(seconds) {
    var d = Math.floor(seconds / 86400);
    var h = Math.floor((seconds % 86400) / 3600);
    var m = Math.floor((seconds % 3600) / 60);
    var s = Math.floor(seconds % 60);
    if (d > 0) return d + 'd ' + h + 'h ' + m + 'm';
    if (h > 0) return h + 'h' + String(m).padStart(2, '0') + 'm' + String(s).padStart(2, '0') + 's';
    if (m > 0) return m + 'm' + String(s).padStart(2, '0') + 's';
    return s + 's';
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
    fetch('../status/trace.json')
      .then(function(r) { return r.ok ? r.json() : Promise.reject(r.status); })
      .then(function(data) {
        renderTrace(data);
        if (data.state === 'LOST') {
          if (traceTick) clearInterval(traceTick);
          traceTick = setInterval(function() { renderTrace(data); }, 1000);
        } else {
          if (traceTick) { clearInterval(traceTick); traceTick = null; }
        }
      })
      .catch(function() {
        var el = document.getElementById('traceStatus');
        if (el) el.innerHTML = '';
      });
  }

  /* ── Fetch responses ─────────────────────────────────── */
  function loadResponses() {
    container.innerHTML = '<div class="tr-loading">LOADING...</div>';
    fetch(WORKER_URL + '/responses?sort=timestamp&order=desc')
      .then(function (r) { return r.ok ? r.json() : Promise.reject(); })
      .then(function (data) {
        allResponses = data.responses || [];
        render();
      })
      .catch(function () {
        container.innerHTML = '<div class="tr-empty">Failed to load responses &mdash; worker unreachable</div>';
      });
  }

  /* ── Filter and sort ─────────────────────────────────── */
  function getFiltered() {
    var search = (searchEl.value || '').toLowerCase();
    var sortVal = sortEl.value;
    var filtered = allResponses;

    if (search) {
      filtered = filtered.filter(function (r) {
        return (r.callsign || '').toLowerCase().indexOf(search) !== -1
            || (r.question || '').toLowerCase().indexOf(search) !== -1
            || (r.answer || '').toLowerCase().indexOf(search) !== -1;
      });
    }

    var sortField = 'timestamp';
    var sortOrder = 'desc';
    if (sortVal === 'timestamp_asc') { sortField = 'timestamp'; sortOrder = 'asc'; }
    else if (sortVal === 'callsign') { sortField = 'callsign'; sortOrder = 'asc'; }

    filtered.sort(function (a, b) {
      if (sortField === 'callsign') {
        return sortOrder === 'asc'
          ? (a.callsign || '').localeCompare(b.callsign || '')
          : (b.callsign || '').localeCompare(a.callsign || '');
      }
      return sortOrder === 'asc'
        ? (a.timestamp || 0) - (b.timestamp || 0)
        : (b.timestamp || 0) - (a.timestamp || 0);
    });

    return filtered;
  }

  /* ── Render table ────────────────────────────────────── */
  function render() {
    var filtered = getFiltered();
    countEl.textContent = filtered.length + ' / ' + allResponses.length + ' responses';

    if (filtered.length === 0) {
      container.innerHTML = '<div class="tr-empty">' + (allResponses.length === 0 ? 'No responses recorded yet.' : 'No responses match your filter.') + '</div>';
      return;
    }

    var html = '<table class="tr-table"><thead><tr>';
    html += '<th class="col-ts">Timestamp</th>';
    html += '<th class="col-callsign">Callsign</th>';
    html += '<th class="col-question">Question</th>';
    html += '<th class="col-answer">Answer</th>';
    html += '</tr></thead><tbody>';

    filtered.forEach(function (r) {
      var d = new Date(r.timestamp);
      var ts = d.toISOString().slice(0, 19).replace('T', ' ');
      html += '<tr>';
      html += '<td class="col-ts">' + escHtml(ts) + '</td>';
      html += '<td class="col-callsign">' + escHtml(r.callsign || '?') + '</td>';
      html += '<td class="col-question">' + escHtml((r.question || '').slice(0, 200)) + '</td>';
      html += '<td class="col-answer">' + escHtml((r.answer || '').slice(0, 500)) + '</td>';
      html += '</tr>';
    });

    html += '</tbody></table>';
    container.innerHTML = html;
  }

  function escHtml(s) {
    var e = document.createElement('div');
    e.textContent = s;
    return e.innerHTML;
  }

  /* ── Export to markdown ──────────────────────────────── */
  function exportMarkdown() {
    var filtered = getFiltered();
    if (filtered.length === 0) return;

    var now = new Date().toISOString().slice(0, 10);
    var lines = ['# Voight-Kampff Responses \u2014 ' + now, ''];

    filtered.forEach(function (r) {
      var d = new Date(r.timestamp);
      var ts = d.toISOString().slice(0, 19).replace('T', ' ');
      lines.push('**' + (r.callsign || 'anon') + '** (' + ts + ')');
      if (r.question) lines.push('> ' + r.question);
      lines.push('> ' + (r.answer || ''));
      lines.push('');
    });

    var text = lines.join('\n');

    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(function () {
        exportBtn.textContent = '\u2713 copied';
        exportBtn.classList.add('copied');
        setTimeout(function () { exportBtn.textContent = 'export md'; exportBtn.classList.remove('copied'); }, 2000);
      }).catch(function () { fallbackCopy(text); });
    } else {
      fallbackCopy(text);
    }
  }

  function fallbackCopy(text) {
    var ta = document.createElement('textarea');
    ta.value = text;
    ta.style.position = 'fixed'; ta.style.left = '-9999px';
    document.body.appendChild(ta);
    ta.select();
    try { document.execCommand('copy'); exportBtn.textContent = '\u2713 copied'; } catch { exportBtn.textContent = 'err'; }
    setTimeout(function () { exportBtn.textContent = 'export md'; }, 2000);
    document.body.removeChild(ta);
  }

  /* ── Events ───────────────────────────────────────────── */
  searchEl.addEventListener('input', render);
  sortEl.addEventListener('change', render);
  exportBtn.addEventListener('click', exportMarkdown);

  /* ── Periodic refresh ────────────────────────────────── */
  setInterval(function () {
    fetch(WORKER_URL + '/responses?sort=timestamp&order=desc')
      .then(function (r) { return r.ok ? r.json() : Promise.reject(); })
      .then(function (data) {
        allResponses = data.responses || [];
        render();
      })
      .catch(function () {});
  }, 30000);

  /* ── Init ─────────────────────────────────────────────── */
  setOperator();
  updateTrace();
  setInterval(updateTrace, 30000);
  loadResponses();
})();
