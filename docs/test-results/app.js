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

  /* ── Fetch responses ─────────────────────────────────── */
  function loadResponses() {
    container.innerHTML = '<div class="results-loading">LOADING...</div>';
    var params = '?sort=timestamp&order=desc';
    fetch(WORKER_URL + '/responses' + params)
      .then(function (r) { return r.ok ? r.json() : Promise.reject(); })
      .then(function (data) {
        allResponses = data.responses || [];
        render();
      })
      .catch(function () {
        container.innerHTML = '<div class="results-empty">Failed to load responses. Check worker URL and CORS settings.</div>';
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
      container.innerHTML = '<div class="results-empty">' + (allResponses.length === 0 ? 'No responses recorded yet.' : 'No responses match your filter.') + '</div>';
      return;
    }

    var html = '<table class="results-table"><thead><tr>';
    html += '<th class="col-ts">Timestamp</th>';
    html += '<th class="col-callsign">Callsign</th>';
    html += '<th class="col-question">Question</th>';
    html += '<th class="col-answer">Answer</th>';
    html += '</tr></thead><tbody>';

    filtered.forEach(function (r) {
      var d = new Date(r.timestamp);
      var ts = d.toISOString().slice(0, 19).replace('T', ' ');
      var q = (r.question || '').slice(0, 200);
      var a = (r.answer || '').slice(0, 500);
      html += '<tr>';
      html += '<td class="col-ts">' + escHtml(ts) + '</td>';
      html += '<td class="col-callsign">' + escHtml(r.callsign || '?') + '</td>';
      html += '<td class="col-question">' + escHtml(q) + '</td>';
      html += '<td class="col-answer">' + escHtml(a) + '</td>';
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
    var lines = [];
    lines.push('# Voight-Kampff Responses \u2014 ' + now);
    lines.push('');

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
        setTimeout(function () {
          exportBtn.textContent = 'export md';
          exportBtn.classList.remove('copied');
        }, 2000);
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

  /* ── Trace status ────────────────────────────────────── */
  function updateTrace() {
    fetch('../status/trace.json')
      .then(function (r) { return r.ok ? r.json() : Promise.reject(); })
      .then(function (data) {
        var el = document.getElementById('traceStatus');
        if (!el) return;
        if (data.state === 'ACTIVE') {
          el.innerHTML = '<span class="trace-dot"></span> TRACE: ACTIVE';
        } else if (data.state === 'LOST' && data.lastSeenAt) {
          el.innerHTML = '<span class="trace-dot trace-lost"></span> TRACE: LOST';
        } else {
          el.innerHTML = '';
        }
      })
      .catch(function () {});
  }

  /* ── Periodic refresh ────────────────────────────────── */
  function startRefresh() {
    setInterval(function () {
      fetch(WORKER_URL + '/responses?sort=timestamp&order=desc')
        .then(function (r) { return r.ok ? r.json() : Promise.reject(); })
        .then(function (data) {
          allResponses = data.responses || [];
          render();
        })
        .catch(function () {});
    }, 30000);
  }

  /* ── Init ─────────────────────────────────────────────── */
  setOperator();
  updateTrace();
  loadResponses();
  startRefresh();
})();
