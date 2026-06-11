/* ── CONFIG ────────────────────────────────────────────── */
const ALPHA = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ';
const KEY   = 'MINDFAGEBJRLHCVPQSKYUWOXTZ';

/* ── STATE ─────────────────────────────────────────────── */
let activeTab = 'schl_code';

/* ── CIPHER ────────────────────────────────────────────── */
function substitute(text, fromChars, toChars) {
  return text.split('').map(function(ch) {
    var upper = ch.toUpperCase();
    var idx = fromChars.indexOf(upper);
    if (idx === -1) return ch;
    var result = toChars[idx];
    return ch === upper ? result : result.toLowerCase();
  }).join('');
}

function schoolCodeDecrypt(text) {
  return substitute(text, ALPHA, KEY);
}

function schoolCodeEncrypt(text) {
  return substitute(text, KEY, ALPHA);
}

/* ── TRANSLATION (MyMemory API) ────────────────────────── */
function translate(text, langpair) {
  var url = 'https://api.mymemory.translated.net/get?q=' + encodeURIComponent(text) +
            '&langpair=' + encodeURIComponent(langpair);
  return fetch(url)
    .then(function(r) { return r.json(); })
    .then(function(data) {
      var translated = data.responseData && data.responseData.translatedText;
      if (!translated) throw new Error(data.responseDetails || 'Translation failed');
      return translated;
    });
}

/* ── TOOL: SCHL_CODE ───────────────────────────────────── */
function runSchlCode() {
  var input = document.getElementById('schlCodeInput');
  var output = document.getElementById('schlCodeOutput');
  var modeEl = document.getElementById('schlCodeMode');
  if (!input || !output) return;

  var text = input.value;
  var mode = modeEl ? modeEl.textContent : 'DECRYPT';

  if (!text) { output.textContent = '(no input)'; return; }

  if (mode === 'DECRYPT') {
    output.textContent = schoolCodeDecrypt(text);
  } else {
    output.textContent = schoolCodeEncrypt(text);
  }
  updateCharCount('schlCodeCount', text.length);
}

function toggleSchlMode() {
  var btn = document.getElementById('schlCodeMode');
  if (!btn) return;
  if (btn.textContent === 'DECRYPT') {
    btn.textContent = 'ENCRYPT';
  } else {
    btn.textContent = 'DECRYPT';
  }
  runSchlCode();
}

/* ── TOOL: SCHL_FR_EN ──────────────────────────────────── */
function runSchlFrEn() {
  var input = document.getElementById('schlFrEnInput');
  var output = document.getElementById('schlFrEnOutput');
  if (!input || !output) return;

  var text = input.value;
  if (!text) { output.textContent = '(no input)'; return; }

  var decrypted = schoolCodeDecrypt(text);
  output.className = 'tool-output is-loading';
  output.textContent = 'translating...';

  translate(decrypted, 'fr|en')
    .then(function(translated) {
      output.className = 'tool-output';
      output.textContent = decrypted + '\n\u2500'.repeat(40) + '\n' + translated;
    })
    .catch(function(err) {
      output.className = 'tool-output is-error';
      output.textContent = 'Translation error: ' + err.message + '\n\nDecrypted text:\n' + decrypted;
    });
  updateCharCount('schlFrEnCount', text.length);
}

/* ── TOOL: FR_EN (reverse) ─────────────────────────────── */
function runFrEn() {
  var input = document.getElementById('frEnInput');
  var output = document.getElementById('frEnOutput');
  var direction = document.getElementById('frEnDirection');
  if (!input || !output || !direction) return;

  var text = input.value;
  if (!text) { output.textContent = '(no input)'; return; }

  var langpair = direction.textContent === 'EN\u2192FR' ? 'en|fr' : 'fr|en';
  output.className = 'tool-output is-loading';
  output.textContent = 'translating...';

  translate(text, langpair)
    .then(function(translated) {
      output.className = 'tool-output';
      output.textContent = text + '\n\u2500'.repeat(40) + '\n' + translated;
    })
    .catch(function(err) {
      output.className = 'tool-output is-error';
      output.textContent = 'Translation error: ' + err.message;
    });
  updateCharCount('frEnCount', text.length);
}

function toggleFrEnDirection() {
  var btn = document.getElementById('frEnDirection');
  if (!btn) return;
  if (btn.textContent === 'EN\u2192FR') {
    btn.textContent = 'FR\u2192EN';
  } else {
    btn.textContent = 'EN\u2192FR';
  }
  runFrEn();
}

/* ── CHAR COUNT ────────────────────────────────────────── */
function updateCharCount(id, len) {
  var el = document.getElementById(id);
  if (el) el.textContent = len + ' chars';
}

/* ── TABS ──────────────────────────────────────────────── */
function switchTab(tabId) {
  activeTab = tabId;

  document.querySelectorAll('.tool-tab').forEach(function(t) {
    t.classList.toggle('active', t.dataset.tab === tabId);
  });
  document.querySelectorAll('.tool-pane').forEach(function(p) {
    p.classList.toggle('active', p.id === 'pane-' + tabId);
  });
}

/* ── EYE CANVAS (dot-matrix VK scanner) ────────────────── */
(function initEye() {
  var canvas = document.getElementById('eyeCanvas');
  if (!canvas) return;

  var ctx = canvas.getContext('2d');
  var seed = (localStorage.getItem('operator') || '').trim() || 'anon';
  var seedNum = 0;
  for (var i = 0; i < seed.length; i++) {
    seedNum = ((seedNum << 5) - seedNum) + seed.charCodeAt(i);
    seedNum |= 0;
  }
  var rng = function() {
    seedNum = (seedNum * 1103515245 + 12345) & 0x7fffffff;
    return (seedNum >>> 0) / 0x7fffffff;
  };

  var BW = 105, BH = 105;
  var dpr = window.devicePixelRatio || 1;
  canvas.width = BW * dpr;
  canvas.height = BH * dpr;
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

  var cx = BW / 2, cy = BH / 2;
  var gridW = 28, gridH = 28;
  var dotSize = 3;
  var time = 0;
  var scanDir = 1;
  var scanPos = 0;

  var lookX = 0, lookY = 0;
  var dartTimer = 0;
  var redTimer = 0;
  var nextRedFlicker = 15 + rng() * 30;

  var GRID = {};
  var gazeTargetX = 0, gazeTargetY = 0;
  var gazeHold = 3;
  function updateLook() {
    gazeHold += 0.016;
    var microX = Math.sin(time * 11.3) * 0.012 + Math.sin(time * 17.7) * 0.01;
    var microY = Math.sin(time * 9.1) * 0.01 + Math.sin(time * 23.5) * 0.007;
    if (gazeHold > 2.5 + Math.random() * 3) {
      gazeHold = 0;
      if (Math.random() < 0.55) {
        gazeTargetX = 0; gazeTargetY = 0;
      } else {
        var angle = Math.random() * Math.PI * 2;
        var dist = 0.1 + Math.random() * 0.2;
        gazeTargetX = Math.cos(angle) * dist;
        gazeTargetY = Math.sin(angle) * dist * 0.7;
      }
    }
    lookX += (gazeTargetX + microX - lookX) * 0.07;
    lookY += (gazeTargetY + microY - lookY) * 0.07;
  }

  function getCell(gx, gy) {
    var k = gx + ',' + gy;
    if (GRID[k] !== undefined) return GRID[k];
    var val = 0;

    var zoom = 0.35;
    var nx = ((gx / gridW) * 2 - 1) * zoom;
    var ny = ((gy / gridH) * 2 - 1) * zoom;
    var eyeTilt = Math.sin(time * 0.3) * 0.04;

    /* Brow ridge / forehead area - subtle skin contour */
    var underBrow = ny < -0.2;
    var browDist = Math.abs(ny + 0.35);
    if (underBrow && Math.abs(nx) < 0.75 && browDist < 0.2) {
      val = Math.max(val, 20 - browDist * 120);
    }

    /* Eye socket depression */
    var socketX = nx, socketY = ny - eyeTilt;
    var socketDist = Math.sqrt(socketX * socketX / 0.45 / 0.45 + socketY * socketY / 0.55 / 0.55);
    if (socketDist < 0.7 && socketDist > 0.2) {
      val = Math.max(val, 15 + (1 - socketDist / 0.7) * 15);
    }

    /* Inner corner (caruncle) */
    var carX = nx + 0.3, carY = ny - eyeTilt + 0.05;
    var carD = Math.sqrt(carX * carX * 3 + carY * carY * 6);
    if (carD < 0.12) {
      val = Math.max(val, 50 - carD * 300);
    }

    /* Sclera (eyeball) */
    var sX = nx * 0.85, sY = (ny - eyeTilt) * 0.85;
    var sDist = Math.sqrt(sX * sX / 0.55 / 0.55 + sY * sY / 0.38 / 0.38);
    if (sDist < 1) {
      var sBright = (1 - sDist) * 230 + 20;
      val = Math.max(val, sBright);

      /* Blood vessel hints */
      for (var vi = 0; vi < 4; vi++) {
        var va = -Math.PI / 2 + (vi / 4) * Math.PI + rng() * 0.1;
        var vdist = Math.abs((nx + 0.4) * Math.cos(va) + (ny - eyeTilt) * Math.sin(va) * 0.5);
        if (vdist < 0.04 && sDist > 0.25) {
          val = Math.max(val, 55 - vdist * 700);
        }
      }
    }

    /* Iris (follows look offset) */
    var iX = nx + lookX, iY = ny - eyeTilt + lookY;
    var iDist = Math.sqrt(iX * iX / 0.20 / 0.20 + iY * iY / 0.19 / 0.19);
    if (iDist < 1) {
      var iBright = 40 + (1 - iDist) * 80;
      /* Iris striae */
      var angle = Math.atan2(iY, iX);
      var streak = Math.abs(Math.cos(angle * 5 + iDist * 3)) * 0.25;
      iBright = iBright - streak * 15;
      val = Math.max(val, iBright);
    }

    /* Pupil (follows look offset) */
    var pDist = Math.sqrt(iX * iX / 0.11 / 0.11 + iY * iY / 0.10 / 0.10);
    if (pDist < 1) {
      val = 0;
    }

    /* Catchlight (follows look offset) */
    var clX = nx + lookX + 0.04, clY = (ny - eyeTilt + lookY) + 0.05;
    var clDist = Math.sqrt(clX * clX / 0.06 / 0.06 + clY * clY / 0.04 / 0.04);
    if (clDist < 1) {
      val = Math.max(val, (1 - clDist) * 180);
    }

    /* Eyelids - dark arcs */
    var lidTop = cy - BH * 0.12 - eyeTilt * BH * 0.5;
    var lidBot = cy + BH * 0.16 - eyeTilt * BH * 0.5;
    var lx = gx - gridW / 2;
    var ly = gy - gridH / 2;
    var lidSpread = gridW * 0.32;

    var topLidY = lidTop + Math.pow(lx / lidSpread, 2) * 10;
    if (ly * BH / gridH < topLidY - 1) {
      val = Math.max(val, 10);
    } else if (ly * BH / gridH < topLidY) {
      var edge = (topLidY - ly * BH / gridH);
      val = Math.max(val, edge * 25);
    }

    var botLidY = lidBot - Math.pow(lx / lidSpread, 2) * 8;
    if (ly * BH / gridH > botLidY + 1) {
      val = Math.max(val, 10);
    } else if (ly * BH / gridH > botLidY) {
      var edge2 = (ly * BH / gridH - botLidY);
      val = Math.max(val, edge2 * 25);
    }

    /* Eyelash hints */
    var lashY = BH * (0.5 - 0.16) - eyeTilt * BH * 0.5 - 2;
    if (Math.abs(ly * BH / gridH - lashY) < 1.5 && Math.abs(lx) < gridW * 0.25) {
      var lashPattern = Math.abs(Math.sin(lx * 1.7 + rng())) > 0.4;
      if (lashPattern) val = Math.max(val, 80);
    }

    /* Under-eye bags / skin detail */
    var bagY = BH * (0.5 + 0.22) - eyeTilt * BH * 0.5;
    if (Math.abs(ly * BH / gridH - bagY) < 2 && Math.abs(lx) < gridW * 0.2) {
      val = Math.max(val, 25 - Math.abs(ly * BH / gridH - bagY) * 4);
    }

    GRID[k] = Math.min(Math.round(val), 255);
    return GRID[k];
  }

  function renderDotArt() {
    ctx.fillStyle = '#000';
    ctx.fillRect(0, 0, BW, BH);

    var pixelsPerCellW = BW / gridW;
    var pixelsPerCellH = BH / gridH;

    for (var gy = 0; gy < gridH; gy++) {
      for (var gx = 0; gx < gridW; gx++) {
        var val = getCell(gx, gy);
        if (val < 8) continue;

        var px = (gx + 0.5) * pixelsPerCellW;
        var py = (gy + 0.5) * pixelsPerCellH;

        var size = (val / 255) * dotSize * 0.9;
        if (size < 0.5) continue;

        var alpha = Math.min(0.7, val / 280);
        if (redTimer > 0) {
          var rBright = 200 + Math.floor(Math.random() * 55);
          ctx.fillStyle = 'rgba(' + rBright + ',20,10,' + alpha + ')';
        } else {
          ctx.fillStyle = 'rgba(180,180,180,' + alpha + ')';
        }
        ctx.fillRect(px - size / 2, py - size / 2, size, size);
      }
    }

    /* Red bouncing scanline - full width */
    var scanY = scanPos;
    ctx.fillStyle = 'rgba(221,0,0,0.25)';
    ctx.fillRect(0, Math.max(0, scanY - 3), BW, 1);
    ctx.fillRect(0, Math.max(0, scanY + 3), BW, 1);
    ctx.fillStyle = 'rgba(221,0,0,0.5)';
    ctx.fillRect(0, Math.max(0, scanY - 2), BW, 1);
    ctx.fillRect(0, Math.max(0, scanY + 2), BW, 1);
    ctx.fillStyle = 'rgba(221,0,0,0.8)';
    ctx.fillRect(0, Math.max(0, scanY - 1), BW, 1);
    ctx.fillRect(0, Math.max(0, scanY + 1), BW, 1);
    ctx.fillStyle = 'rgba(255,60,60,1)';
    ctx.fillRect(0, scanY, BW, 1);

    /* Glow bloom around scanline */
    var glowGrad = ctx.createLinearGradient(0, scanY - 10, 0, scanY + 10);
    glowGrad.addColorStop(0, 'rgba(221,0,0,0)');
    glowGrad.addColorStop(0.3, 'rgba(221,0,0,0.06)');
    glowGrad.addColorStop(0.5, 'rgba(221,0,0,0.15)');
    glowGrad.addColorStop(0.7, 'rgba(221,0,0,0.06)');
    glowGrad.addColorStop(1, 'rgba(221,0,0,0)');
    ctx.fillStyle = glowGrad;
    ctx.fillRect(0, scanY - 10, BW, 20);

    /* Bottom glitch line — flickering dot row */
    ctx.fillStyle = 'rgba(180,180,180,0.15)';
    for (var gi = 0; gi < gridW; gi++) {
      if (Math.random() < 0.4) continue;
      var gxPos = (gi + 0.5) * pixelsPerCellW;
      var gyPos = BH - 4 + (Math.random() - 0.5) * 3;
      var gSize = 1 + Math.random() * 2;
      ctx.fillRect(gxPos - gSize / 2, gyPos - gSize / 2, gSize, gSize);
    }
  }

  function animate() {
    updateLook();
    if (redTimer > 0) {
      redTimer += 0.016;
      if (redTimer > 1) redTimer = 0;
    } else {
      nextRedFlicker -= 0.016;
      if (nextRedFlicker <= 0) {
        redTimer = 0.001;
        nextRedFlicker = 20 + rng() * 40;
      }
    }
    GRID = {};
    renderDotArt();

    scanPos += scanDir * 0.35;
    if (scanPos >= BH - 1) { scanPos = BH - 1; scanDir = -1; }
    if (scanPos <= 0) { scanPos = 0; scanDir = 1; }

    time += 0.016;
    requestAnimationFrame(animate);
  }
  animate();
})();

/* ── TRACE (Discourse online status) ───────────────────── */
var traceTick = null;

function fmtElapsed(seconds) {
  var h = Math.floor(seconds / 3600);
  var m = Math.floor((seconds % 3600) / 60);
  var s = Math.floor(seconds % 60);
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

/* ── OPERATOR ──────────────────────────────────────────── */
function setOperator() {
  var el = document.getElementById('operatorDisplay');
  if (!el) return;
  var name = localStorage.getItem('operator') || '';
  el.textContent = name ? 'Operator: ' + name : 'Operator: <anon>';
}

/* ── AUTO-RUN ON TAB ENTER ─────────────────────────────── */
function setupEnterTriggers() {
  document.getElementById('schlCodeInput').addEventListener('input', runSchlCode);
  document.getElementById('schlFrEnInput').addEventListener('input', debounce(runSchlFrEn, 400));
  document.getElementById('frEnInput').addEventListener('input', debounce(runFrEn, 400));
}

function debounce(fn, ms) {
  var timer;
  return function() {
    var args = arguments;
    var ctx = this;
    clearTimeout(timer);
    timer = setTimeout(function() { fn.apply(ctx, args); }, ms);
  };
}

/* ── INIT ──────────────────────────────────────────────── */
document.addEventListener('DOMContentLoaded', function() {
  setOperator();
  updateTrace();
  setInterval(updateTrace, 30000);

  /* Tab switching */
  document.querySelectorAll('.tool-tab').forEach(function(tab) {
    tab.addEventListener('click', function() {
      switchTab(this.dataset.tab);
    });
  });

  setupEnterTriggers();
  runSchlCode();
});
