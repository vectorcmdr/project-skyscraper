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

/* ── TRANSLATION (Bergamot WASM) ───────────────────────────
 *   window.translateText(text, from, to) is loaded by
 *   translator/bergamot.js as a module script.
 */

/* ── TOOL: SCHL_CODE ───────────────────────────────────── */
function runSchlCode() {
  var input = document.getElementById('schlCodeInput');
  var output = document.getElementById('schlCodeOutput');
  var modeEl = document.getElementById('schlCodeMode');
  if (!input || !output) return;

  var text = input.value;
  var mode = modeEl ? modeEl.textContent : 'MODE: DECRYPT';

  if (!text) { output.textContent = '(no input)'; return; }

  if (mode === 'MODE: DECRYPT') {
    output.textContent = schoolCodeDecrypt(text);
  } else {
    output.textContent = schoolCodeEncrypt(text);
  }
  updateCharCount('schlCodeCount', text.length);
}

function toggleSchlMode() {
  var btn = document.getElementById('schlCodeMode');
  if (!btn) return;
  if (btn.textContent === 'MODE: DECRYPT') {
    btn.textContent = 'MODE: ENCRYPT';
  } else {
    btn.textContent = 'MODE: DECRYPT';
  }
  runSchlCode();
}

/* ── TOOL: SCHL_FR_EN ──────────────────────────────────── */
var schlFrEnReqId = 0;
async function runSchlFrEn() {
  var input = document.getElementById('schlFrEnInput');
  var decEl = document.getElementById('schlFrEnDecrypted');
  var trEl = document.getElementById('schlFrEnTranslated');
  if (!input || !decEl || !trEl) return;

  var text = input.value;
  if (!text) { decEl.textContent = '(no input)'; trEl.textContent = '(awaiting translation)'; return; }

  var reqId = ++schlFrEnReqId;
  var decrypted = schoolCodeDecrypt(text);

  decEl.className = 'tool-output';
  decEl.textContent = decrypted;

  trEl.className = 'tool-output is-loading';
  trEl.textContent = 'Initializing translator (downloading models ~26 MB on first use)...';
  if (window._showDownloadProgress) window._showDownloadProgress();

  try {
    var result = await window.translateText(decrypted, 'fr', 'en');
    if (reqId !== schlFrEnReqId) return;
    trEl.className = 'tool-output';
    trEl.textContent = result;
    if (window._hideDownloadProgress) window._hideDownloadProgress();
  } catch (e) {
    if (reqId !== schlFrEnReqId) return;
    trEl.className = 'tool-output is-error';
    trEl.textContent = 'Translation error: ' + e.message;
    if (window._hideDownloadProgress) window._hideDownloadProgress();
  }
  updateCharCount('schlFrEnCount', text.length);
}

/* ── TOOL: FR_EN (reverse) ─────────────────────────────── */
var frEnReqId = 0;
async function runFrEn() {
  var input = document.getElementById('frEnInput');
  var output = document.getElementById('frEnOutput');
  var direction = document.getElementById('frEnDirection');
  if (!input || !output || !direction) return;

  var text = input.value;
  if (!text) { output.textContent = '(no input)'; return; }

  var reqId = ++frEnReqId;
  var from = direction.textContent === 'EN\u2192FR' ? 'en' : 'fr';
  var to = direction.textContent === 'EN\u2192FR' ? 'fr' : 'en';

  output.className = 'tool-output is-loading';
  output.textContent = 'Initializing translator (downloading models ~26 MB on first use)...';
  if (window._showDownloadProgress) window._showDownloadProgress();

  try {
    var result = await window.translateText(text, from, to);
    if (reqId !== frEnReqId) return;
    output.className = 'tool-output';
    output.textContent = result;
    if (window._hideDownloadProgress) window._hideDownloadProgress();
  } catch (e) {
    if (reqId !== frEnReqId) return;
    output.className = 'tool-output is-error';
    output.textContent = 'Translation error: ' + e.message;
    if (window._hideDownloadProgress) window._hideDownloadProgress();
  }
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

/* ── TOOL: TS_CONV (Timestamp Converter) ──────────────── */

function _parseDate(value) {
  if (!value) return null;
  var parts = value.split('-');
  if (parts.length !== 3) return null;
  return {
    year: parseInt(parts[0], 10),
    month: parseInt(parts[1], 10) - 1,
    day: parseInt(parts[2], 10)
  };
}

function _parseTime(str) {
  if (!str || !str.trim()) return { h: 0, m: 0, s: 0 };
  var parts = str.trim().split(':');
  return {
    h: parseInt(parts[0], 10) || 0,
    m: parseInt(parts[1], 10) || 0,
    s: parseInt(parts[2], 10) || 0
  };
}

function _pad2(n) {
  return n < 10 ? '0' + n : '' + n;
}

function runTsConvDate() {
  var dateInput = document.getElementById('tsConvDateInput');
  var timeInput = document.getElementById('tsConvTimeInput');
  var output = document.getElementById('tsConvEpochOutput');
  var modeEl = document.getElementById('tsConvDateMode');
  if (!dateInput || !output || !modeEl) return;

  var dateVal = dateInput.value;
  var timeStr = timeInput ? timeInput.value : '';

  if (!dateVal) {
    output.textContent = '(enter a date)';
    return;
  }

  var d = _parseDate(dateVal);
  if (!d) { output.textContent = 'invalid date'; return; }

  var t = _parseTime(timeStr);
  var isUTC = modeEl.textContent === 'UTC';

  var date = isUTC
    ? new Date(Date.UTC(d.year, d.month, d.day, t.h, t.m, t.s))
    : new Date(d.year, d.month, d.day, t.h, t.m, t.s);

  var epoch = Math.floor(date.getTime() / 1000);
  if (isNaN(epoch)) { output.textContent = 'invalid date'; return; }

  output.textContent = epoch;
}

function runTsConvEpoch() {
  var epochInput = document.getElementById('tsConvEpochInput');
  var output = document.getElementById('tsConvDateOutput');
  var modeEl = document.getElementById('tsConvEpochMode');
  if (!epochInput || !output || !modeEl) return;

  var val = epochInput.value.trim();
  if (!val) { output.textContent = '(enter an epoch)'; return; }

  var secs = parseInt(val, 10);
  if (isNaN(secs)) { output.textContent = 'invalid epoch (must be integer seconds)'; return; }

  var isUTC = modeEl.textContent === 'UTC';
  var date = new Date(secs * 1000);

  var day, month, year, h, m, s;
  if (isUTC) {
    day = _pad2(date.getUTCDate());
    month = _pad2(date.getUTCMonth() + 1);
    year = date.getUTCFullYear();
    h = _pad2(date.getUTCHours());
    m = _pad2(date.getUTCMinutes());
    s = _pad2(date.getUTCSeconds());
  } else {
    day = _pad2(date.getDate());
    month = _pad2(date.getMonth() + 1);
    year = date.getFullYear();
    h = _pad2(date.getHours());
    m = _pad2(date.getMinutes());
    s = _pad2(date.getSeconds());
  }

  output.textContent = day + '-' + month + '-' + year + ' ' + h + ':' + m + ':' + s;
}

function toggleTsConvDateMode() {
  var btn = document.getElementById('tsConvDateMode');
  if (!btn) return;
  btn.textContent = btn.textContent === 'UTC' ? 'LOCAL' : 'UTC';
  runTsConvDate();
}

function toggleTsConvEpochMode() {
  var btn = document.getElementById('tsConvEpochMode');
  if (!btn) return;
  btn.textContent = btn.textContent === 'UTC' ? 'LOCAL' : 'UTC';
  runTsConvEpoch();
}

function _initTsConv() {
  /* Set default date to today, time to now */
  var now = new Date();
  var di = document.getElementById('tsConvDateInput');
  var ti = document.getElementById('tsConvTimeInput');
  if (di) di.value = now.getFullYear() + '-' + _pad2(now.getMonth() + 1) + '-' + _pad2(now.getDate());
  if (ti) ti.value = _pad2(now.getHours()) + ':' + _pad2(now.getMinutes()) + ':' + _pad2(now.getSeconds());

  var ei = document.getElementById('tsConvEpochInput');
  if (ei) ei.value = Math.floor(now.getTime() / 1000);

  runTsConvDate();
  runTsConvEpoch();
}

/* ── CHAR COUNT ────────────────────────────────────────── */
function updateCharCount(id, len) {
  var el = document.getElementById(id);
  if (el) el.textContent = len + ' chars';
}

/* ── COPY TO CLIPBOARD ────────────────────────────────── */
function copyToClipboard(id) {
  var el = document.getElementById(id);
  if (!el) return;
  var text = el.textContent;
  if (!text || text === '(no input)' || text === '(awaiting translation)' || text === '(awaiting cipher output)') return;
  var btn = document.querySelector('[data-target="' + id + '"]');
  function ok() {
    if (!btn) return;
    btn.classList.add('copied');
    btn.textContent = '\u2713';
    setTimeout(function() {
      btn.classList.remove('copied');
      btn.textContent = '\u2398';
    }, 1500);
  }
  var ta = document.createElement('textarea');
  ta.value = text;
  ta.style.position = 'fixed';
  ta.style.top = '100px';
  ta.style.left = '100px';
  ta.style.width = '300px';
  ta.style.height = '50px';
  ta.style.opacity = '0.01';
  ta.style.zIndex = '9999';
  document.body.appendChild(ta);
  ta.focus();
  ta.select();
  ta.setSelectionRange(0, text.length);
  var worked = document.execCommand('copy');
  document.body.removeChild(ta);
  if (worked) { ok(); return; }
  /* If execCommand failed, try the modern API */
  navigator.clipboard.writeText(text).then(ok, function() {
    alert('clipboard copy failed - your browser may require HTTPS');
  });
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
  var nextRedFlicker = 10 + rng() * 20;

  /* Blink state — 0=open(rest), 1=closing, 2=hold closed, 3=opening */
  var blinkState = 0;
  var blinkPos = 0;
  var blinkHold = 0;
  var nextBlink = 2 + rng() * 4;

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

  function updateBlink() {
    var dt = 0.016;
    if (blinkState === 0) {
      nextBlink -= dt;
      if (nextBlink <= 0) { blinkState = 1; blinkPos = 0; }
    } else if (blinkState === 1) {
      blinkPos += dt * 12;
      if (blinkPos >= 1) { blinkPos = 1; blinkState = 2; blinkHold = 0; }
    } else if (blinkState === 2) {
      blinkHold += dt;
      if (blinkHold >= 0.08) { blinkState = 3; }
    } else if (blinkState === 3) {
      blinkPos -= dt * 12;
      if (blinkPos <= 0) { blinkPos = 0; blinkState = 0; nextBlink = 2 + rng() * 4; }
    }
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

    /* Iris ring (follows look offset, overrides sclera) */
    var iX = nx + lookX, iY = ny - eyeTilt + lookY;
    var iDist = Math.sqrt(iX * iX / 0.27 / 0.27 + iY * iY / 0.26 / 0.26);
    if (iDist < 1) {
      val = 130 + (1 - iDist) * 70;
      /* Iris striae - radial lines */
      var angle = Math.atan2(iY, iX);
      var streak = Math.abs(Math.cos(angle * 6 + iDist * 4)) * 0.35;
      val = val - streak * 40;
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

    /* Permanent resting eyelids + blink overlay */
    var restingLid = BH * 0.10;
    var blinkExtra = (BH / 2 - restingLid) * blinkPos;
    var lidH = restingLid + blinkExtra;
    ctx.fillStyle = '#000';
    ctx.fillRect(0, 0, BW, lidH);
    ctx.fillRect(0, BH - lidH, BW, lidH);
  }

  function animate() {
    updateLook();
    updateBlink();
    if (redTimer > 0) {
      redTimer += 0.016;
      if (redTimer > 1) redTimer = 0;
    } else {
      nextRedFlicker -= 0.016;
      if (nextRedFlicker <= 0) {
        redTimer = 0.001;
        nextRedFlicker = 10 + rng() * 27;
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

/* ── FINGERPRINT CANVAS (wobbly concentric rings) ──────── */
(function initFP() {
  var canvas = document.getElementById('fpCanvas');
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

  var BW = 78, BH = 105;
  var dpr = window.devicePixelRatio || 1;
  canvas.width = BW * dpr;
  canvas.height = BH * dpr;
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

  var time = 0;
  var cx = BW / 2, cy = BH * 0.50;

  /* Seed the ring count, spacing, base radii */
  var ringCount = 9 + Math.floor(rng() * 4);
  var spacing = 2.6 + rng() * 0.6;
  var baseRx = 3.5 + rng() * 1.5;
  var baseRy = 6.5 + rng() * 2.0;

  /* Shared wobble parameters — same across all rings so they stay concentric */
  var wF1 = 1.8 + rng() * 1.5, wP1 = rng() * 6.283, wA1 = 0.30 + rng() * 0.20;
  var wF2 = 4.0 + rng() * 3.0, wP2 = rng() * 6.283, wA2 = 0.15 + rng() * 0.15;
  var wobbleAmp = 0.8 + rng() * 0.6;

  /* Pick a drift direction — innermost ring shifts from true center,
     outermost ring stays fixed. Rings in between interpolate linearly,
     creating tighter packing on one side and looser on the other. */
  var driftX = (rng() - 0.5) * (8 + rng() * 8);
  var driftY = (rng() - 0.5) * (8 + rng() * 8);

  /* Build each ring — smooth radii, shared wobble, subtle swirl offset */
  var rings = [];
  for (var ri = 0; ri < ringCount; ri++) {
    var rx = baseRx + ri * spacing;
    var ry = baseRy + ri * spacing * 1.35;

    /* Interpolate center: t=1 at innermost (full drift), t=0 at outermost (fixed) */
    var t = (ringCount - 1 - ri) / (ringCount - 1);
    var ringCx = cx + driftX * t;
    var ringCy = cy + driftY * t;

    /* Open-bottom arch — negative = below center (ring extends down),
       positive = above center (ring cut off higher).
       Range: -1.2 to -0.1 keeps all rings below center, outermost reach ~95% down. */
    var gapL = -1.2 + rng() * 1.1;
    var gapR = -1.2 + rng() * 1.1;

    /* Subtle phase drift per ring — cumulative swirl without intersection */
    var swirl = ri * (0.04 + rng() * 0.03);

    var dashLen = 12 + rng() * 28;
    var gapLen = 2 + rng() * 4;

    rings.push({
      cx: ringCx, cy: ringCy,
      rx: rx, ry: ry,
      gL: gapL, gR: gapR,
      sw: swirl,
      wAmp: wobbleAmp, wF1: wF1, wP1: wP1, wA1: wA1,
      wF2: wF2, wP2: wP2, wA2: wA2,
      dash: [dashLen, gapLen]
    });
  }

  function ringPoint(theta, r) {
    var th = theta + r.sw;
    var wob = (Math.sin(th * r.wF1 + r.wP1) * r.wA1
             + Math.sin(th * r.wF2 + r.wP2) * r.wA2) * r.wAmp;
    return {
      x: r.cx + (r.rx + wob) * Math.cos(theta),
      y: r.cy + (r.ry + wob) * Math.sin(theta)
    };
  }

  var scanX = -1, scanY = -1;
  var scanTimer = 4;
  var scanPhase = 0;

  function pickRidgePoint() {
    var ri = Math.floor(rng() * rings.length);
    var r = rings[ri];
    var startTh = -Math.PI + r.gL;
    var endTh = -r.gR;
    var totalTh = endTh - startTh;
    if (totalTh <= 0) totalTh += Math.PI * 2;
    var avgR = (r.rx + r.ry) / 2;
    var totalLen = avgR * totalTh;
    var cycleLen = r.dash[0] + r.dash[1];
    var th;
    if (totalLen >= cycleLen) {
      var numCycles = Math.floor(totalLen / cycleLen);
      var cycle = Math.floor(rng() * numCycles);
      var arcOffset = cycle * cycleLen + rng() * r.dash[0];
      th = startTh + arcOffset / avgR;
    } else {
      th = startTh + rng() * totalTh * (r.dash[0] / cycleLen);
    }
    var p = ringPoint(th, r);
    return { x: Math.round(p.x), y: Math.round(p.y) };
  }

  function renderFP() {
    ctx.fillStyle = '#000';
    ctx.fillRect(0, 0, BW, BH);

    ctx.lineCap = 'round';
    ctx.lineJoin = 'round';

    /* Draw each wobbly ring */
    for (var ri = 0; ri < rings.length; ri++) {
      var r = rings[ri];

      /* Arc from left-bottom, up over top, to right-bottom (open arch) */
      var startTh = -Math.PI + r.gL;
      var endTh = -r.gR;
      var totalTh = endTh - startTh;
      if (totalTh <= 0) totalTh += Math.PI * 2;
      var steps = 24 + Math.floor(r.rx * 0.5);

      /* Main stroke */
      ctx.beginPath();
      var p = ringPoint(startTh, r);
      ctx.moveTo(p.x, p.y);
      for (var si = 1; si <= steps; si++) {
        var t = si / steps;
        var th = startTh + t * totalTh;
        p = ringPoint(th, r);
        ctx.lineTo(p.x, p.y);
      }
      ctx.setLineDash(r.dash);
      ctx.strokeStyle = 'rgba(200,200,200,0.7)';
      ctx.lineWidth = 1.0;
      ctx.stroke();

      /* Second thinner pass with offset dash */
      ctx.setLineDash([r.dash[0] * 0.6, r.dash[1] * 1.3]);
      ctx.strokeStyle = 'rgba(180,180,180,0.35)';
      ctx.lineWidth = 0.6;
      ctx.beginPath();
      p = ringPoint(startTh, r);
      ctx.moveTo(p.x, p.y);
      for (si = 1; si <= steps; si++) {
        t = si / steps;
        th = startTh + t * totalTh;
        p = ringPoint(th, r);
        ctx.lineTo(p.x, p.y);
      }
      ctx.stroke();
    }

    ctx.setLineDash([]);

    /* Red scan box */
    if (scanX >= 0) {
      var pulse = 0.6 + Math.sin(scanPhase * 20) * 0.3;
      ctx.fillStyle = 'rgba(221,0,0,' + (0.5 * pulse) + ')';
      ctx.fillRect(scanX - 2, scanY - 2, 10, 10);
      ctx.fillStyle = 'rgba(255,50,50,' + (0.8 * pulse) + ')';
      ctx.fillRect(scanX, scanY, 6, 6);
      ctx.fillStyle = 'rgba(255,100,100,1)';
      ctx.fillRect(scanX, scanY, 6, 1);
      ctx.fillRect(scanX, scanY, 1, 6);
    }
  }

  function animateFP() {
    scanPhase += 0.016;
    scanTimer -= 0.016;
    if (scanTimer <= 0) {
      if (scanX >= 0) {
        scanX = -1;
        scanTimer = 2 + rng() * 3;
      } else {
        var pos = pickRidgePoint();
        scanX = pos.x;
        scanY = pos.y;
        scanPhase = 0;
        scanTimer = 0.8 + rng() * 0.4;
      }
    }
    renderFP();
    time += 0.016;
    requestAnimationFrame(animateFP);
  }
  animateFP();
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

  var ddi = document.getElementById('tsConvDateInput');
  var dti = document.getElementById('tsConvTimeInput');
  var eei = document.getElementById('tsConvEpochInput');
  if (ddi) ddi.addEventListener('input', runTsConvDate);
  if (dti) dti.addEventListener('input', runTsConvDate);
  if (eei) eei.addEventListener('input', runTsConvEpoch);
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

/* ── VK-STYLE QUERY BAR ────────────────────────────────── */
var _queries = [];
var _queryTimer = null;

function pickQuery() {
  var textEl = document.getElementById('queryText');
  var inputEl = document.getElementById('queryInput');
  var resultEl = document.getElementById('queryResult');
  if (!textEl || !_queries.length) return;
  var q = _queries[Math.floor(Math.random() * _queries.length)];
  textEl.textContent = q;
  if (inputEl) inputEl.value = '';
  if (resultEl) resultEl.textContent = '';
}

function flashPanels() {
  var fp = document.querySelector('.fp-panel');
  var eye = document.querySelector('.eye-panel');
  if (fp) { fp.classList.remove('flash-white'); void fp.offsetWidth; fp.classList.add('flash-white'); }
  if (eye) { eye.classList.remove('flash-white'); void eye.offsetWidth; eye.classList.add('flash-white'); }
  if (_queryTimer) clearTimeout(_queryTimer);
  _queryTimer = setTimeout(function() {
    if (fp) fp.classList.remove('flash-white');
    if (eye) eye.classList.remove('flash-white');
  }, 900);
}

function submitQuery() {
  var inputEl = document.getElementById('queryInput');
  var resultEl = document.getElementById('queryResult');
  if (!inputEl || !resultEl) return;
  var val = inputEl.value.trim();
  if (!val) { resultEl.textContent = '—'; return; }
  resultEl.textContent = '> ' + val;
  flashPanels();
  setTimeout(pickQuery, 1500);
}

function _setOnline(online) {
  var badge = document.getElementById('statusBadge');
  if (!badge) return;
  if (online) {
    badge.textContent = '\u25CF ONLINE';
    badge.className = 'topbar-status status-online';
  } else {
    badge.textContent = '\u25CF OFFLINE';
    badge.className = 'topbar-status status-offline';
  }
}

function initQueryBar() {
  fetch('data/queries.json')
    .then(function(r) { return r.json(); })
    .then(function(list) {
      _queries = list;
      pickQuery();
      _setOnline(true);
    })
    .catch(function() {
      var el = document.getElementById('queryText');
      if (el) el.textContent = '(queries unavailable)';
      _setOnline(false);
    });

  var inputEl = document.getElementById('queryInput');
  var btnEl = document.getElementById('queryBtn');
  if (inputEl) inputEl.addEventListener('keydown', function(e) { if (e.key === 'Enter') submitQuery(); });
  if (btnEl) btnEl.addEventListener('click', submitQuery);
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
  _initTsConv();
  initQueryBar();
});
