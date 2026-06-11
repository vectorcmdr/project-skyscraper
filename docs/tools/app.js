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

/* ── SVG PATH UTILITIES (fingerprint seed variation) ──── */
(function svgUtils() {
  /* Tokenize SVG path string into {cmd, args} array.
     Handles implicit repeated commands (same letter omitted). */
  window._tokenizeSVG = function(str) {
    var tokens = [];
    var re = /([MLCQASZTmlcqaszt])|([-+]?\d*\.?\d+(?:[eE][-+]?\d+)?)/g;
    var m;
    while ((m = re.exec(str)) !== null) {
      if (m[1]) {
        tokens.push({cmd: m[1], args: []});
      } else {
        if (tokens.length === 0) continue;
        tokens[tokens.length - 1].args.push(parseFloat(m[2]));
      }
    }
    return tokens;
  };

  /* Expand implicit repeated commands into individual operations.
     a/A: 7 args each, c/C: 6 args each, m/M: first 2 = moveto, rest = lineto,
     everything else: 2 args each. */
  window._expandImplicit = function(tokens) {
    var out = [];
    for (var i = 0; i < tokens.length; i++) {
      var t = tokens[i];
      var cmd = t.cmd, args = t.args, step;
      if (cmd === 'z' || cmd === 'Z') {
        out.push({cmd: cmd, args: []});
        continue;
      }
      if (cmd === 'a' || cmd === 'A') step = 7;
      else if (cmd === 'c' || cmd === 'C') step = 6;
      else if (cmd === 'm' || cmd === 'M') step = 2;
      else step = 2;

      for (var j = 0; j < args.length; j += step) {
        var piece = args.slice(j, j + step);
        /* After first pair, m/M becomes l/L */
        var useCmd = cmd;
        if ((cmd === 'm' || cmd === 'M') && j > 0) {
          useCmd = cmd === 'm' ? 'l' : 'L';
        }
        out.push({cmd: useCmd, args: piece});
      }
    }
    return out;
  };

  /* Split token array into sub-paths at each M/m command */
  window._splitSubPaths = function(tokens) {
    var paths = [], cur = [];
    for (var i = 0; i < tokens.length; i++) {
      if ((tokens[i].cmd === 'M' || tokens[i].cmd === 'm') && cur.length > 0) {
        paths.push(cur);
        cur = [];
      }
      cur.push(tokens[i]);
    }
    if (cur.length > 0) paths.push(cur);
    return paths;
  };

  /* Build SVG string from sub-paths array */
  window._buildSVG = function(subPaths) {
    var parts = [];
    for (var i = 0; i < subPaths.length; i++) {
      for (var j = 0; j < subPaths[i].length; j++) {
        var t = subPaths[i][j];
        var cmd = t.cmd;
        var numStr = t.args.map(function(n, idx) {
          /* Arc flags at positions 3,4 must be integer 0/1 for Path2D */
          if ((cmd === 'a' || cmd === 'A') && (idx === 3 || idx === 4)) {
            return n >= 0.5 ? '1' : '0';
          }
          if (Math.abs(n) < 0.0005) return '0';
          var s = n.toFixed(3);
          /* Strip leading zero for compactness like original SVG */
          if (s.charAt(0) === '0' && s.length > 1) s = s.slice(1);
          if (s.charAt(0) === '-' && s.charAt(1) === '0' && s.length > 2) {
            s = '-' + s.slice(2);
          }
          return s;
        }).join(' ');
        parts.push(cmd + numStr);
      }
    }
    return parts.join('');
  };

  /* Jitter coordinates of a sub-path. Returns new sub-path array. */
  window._jitterSubPath = function(subPath, rng, strength) {
    return subPath.map(function(t) {
      var cmd = t.cmd, args = t.args;
      var jittered = [];
      for (var i = 0; i < args.length; i++) {
        var val = args[i];
        if ((cmd === 'a' || cmd === 'A') && (i === 3 || i === 4)) {
          /* large-arc-flag and sweep-flag — keep as-is */
          jittered.push(val);
        } else {
          var jit = (rng() - 0.5) * strength * (Math.abs(val) + 0.3);
          jittered.push(val + jit);
        }
      }
      return {cmd: cmd, args: jittered};
    });
  };

  /* Clone a sub-path with scale + position offset.
     The entire sub-path is repositioned by (ox, oy) in viewBox coords.
     Relative command coordinates are scaled. */
  window._cloneSubPath = function(subPath, scale, ox, oy) {
    var clone = [];
    for (var i = 0; i < subPath.length; i++) {
      var t = subPath[i];
      var args = t.args.slice();
      var cmd = t.cmd;

      /* Change relative moveto to absolute so we control exact position */
      if (cmd === 'm') cmd = 'M';

      if (i === 0) {
        /* Offset the first coordinate pair (the moveto) */
        if (args.length >= 2) {
          args[0] = args[0] + ox;
          args[1] = args[1] + oy;
        }
      } else {
        /* Scale relative coords (dx/dy) */
        for (var j = 0; j < args.length; j++) {
          if ((cmd === 'a' || cmd === 'A') && (j === 3 || j === 4)) continue;
          args[j] = args[j] * scale;
        }
      }
      clone.push({cmd: cmd, args: args});
    }
    return clone;
  };

  /* Full pipeline: parse → split → jitter → add extra ridges → rebuild */
  window._generateFingerprintPath = function(svgStr, rng) {
    var tokens = window._expandImplicit(window._tokenizeSVG(svgStr));
    var subPaths = window._splitSubPaths(tokens);

    /* Jitter each sub-path with per-ridge strength */
    var jittered = [];
    for (var i = 0; i < subPaths.length; i++) {
      var sp = subPaths[i];
      var strength = 0.04 + rng() * 0.08;
      jittered.push(window._jitterSubPath(sp, rng, strength));
    }

    /* Add outer-wrap ridges cloned from the first sub-path (outer contour) */
    var outerCount = 1 + Math.floor(rng() * 2); /* 1–2 extra rings */
    for (var i = 0; i < outerCount; i++) {
      var scale = 1.04 + i * 0.04 + rng() * 0.04;
      var ox = (rng() - 0.5) * 1.2;
      var oy = (rng() - 0.5) * 0.8;
      var clone = window._cloneSubPath(subPaths[0], scale, ox, oy, rng);
      /* Apply unique jitter to this clone too */
      clone = window._jitterSubPath(clone, rng, 0.04 + rng() * 0.06);
      jittered.push(clone);
    }

    /* Optionally add 1 inner wrap from first sub-path at smaller scale */
    if (rng() > 0.5) {
      var innerScale = 0.92 + rng() * 0.04;
      var ix = (rng() - 0.5) * 0.6;
      var iy = (rng() - 0.5) * 0.4;
      var inner = window._cloneSubPath(subPaths[0], innerScale, ix, iy, rng);
      inner = window._jitterSubPath(inner, rng, 0.03 + rng() * 0.05);
      jittered.push(inner);
    }

    return window._buildSVG(jittered);
  };
})();

/* ── FINGERPRINT CANVAS ──────────────────────────────── */
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

  /* Reference SVG path data from the fingerprint icon */
  var fpPathSrc = 'M4.16 20.176a.475.475 0 0 1-.439-.294 9.428 9.428 0 0 1 5-12.11.475.475 0 0 1 .364.875A8.464 8.464 0 0 0 4.6 19.521a.474.474 0 0 1-.259.62.48.48 0 0 1-.18.035zm14.544-2.648c1.52-.571 2.17-2.01 1.74-3.853-.686-2.943-4.361-6.932-9.215-6.447a.475.475 0 1 0 .094.944 8.021 8.021 0 0 1 8.198 5.72 2.143 2.143 0 0 1-1.15 2.747c-.853.32-1.816-.386-2.99-1.343a.474.474 0 1 0-.599.735c.911.743 2.005 1.636 3.158 1.636a2.154 2.154 0 0 0 .764-.14zm-3.785 4.917a.475.475 0 0 0-.237-.627c-3.015-1.361-5.06-4.272-5.078-6.135a1.351 1.351 0 0 1 .754-1.358 2.579 2.579 0 0 1 2.614.342.474.474 0 1 0 .493-.811 3.521 3.521 0 0 0-3.514-.389 2.287 2.287 0 0 0-1.296 2.225c.02 2.147 2.181 5.431 5.636 6.99a.475.475 0 0 0 .628-.237zm4.019-1.766a.475.475 0 0 0-.344-.576c-2.603-.658-5.336-2.514-6.357-4.318a.475.475 0 1 0-.826.468c1.307 2.309 4.486 4.147 6.95 4.77a.48.48 0 0 0 .117.014.475.475 0 0 0 .46-.358zm-9.97 2.22a.475.475 0 0 0 .141-.656c-3.359-5.215-2.254-8.739-.287-10.172 1.93-1.407 5.336-1.247 7.848 1.813a.474.474 0 1 0 .733-.601c-2.88-3.512-6.858-3.64-9.14-1.978-2.3 1.675-3.668 5.68.049 11.452a.474.474 0 0 0 .655.142zM4.85 4.397c1.323-1.234 8.372-4.568 13.677-.33a.474.474 0 1 0 .592-.74c-5.494-4.39-12.897-1.51-14.916.377a.474.474 0 1 0 .647.693zm17.347 8.67a.475.475 0 0 0 .378-.555 10.525 10.525 0 0 0-9.397-8.332 10.523 10.523 0 0 0-11.054 6.63.475.475 0 0 0 .87.38c1.872-4.3 5.64-6.57 10.078-6.067a9.58 9.58 0 0 1 8.57 7.565.475.475 0 0 0 .466.387.496.496 0 0 0 .089-.009z';

  /* Generate seed-variant path string, then compile to Path2D */
  var fpPathStr = _generateFingerprintPath(fpPathSrc, rng);
  var fpPath = new Path2D(fpPathStr);

  /* Seed-based variations applied as canvas transforms */
  var flipX = rng() > 0.5 ? 1 : -1;
  var scaleVal = 3.5 + rng() * 0.5;
  var rotateVal = (rng() - 0.5) * 0.04;
  var stretchX = 0.9 + rng() * 0.2;
  var stretchY = 0.9 + rng() * 0.2;
  var offsetX = (rng() - 0.5) * 4;
  var offsetY = (rng() - 0.5) * 3;
  var lineW = 1.4 + rng() * 0.6;
  var dashA = 0.6 + rng() * 0.4;
  var dashB = 0.15 + rng() * 0.15;

  var scanX = -1, scanY = -1;
  var scanTimer = 4;
  var scanPhase = 0;

  function renderFP() {
    ctx.fillStyle = '#000';
    ctx.fillRect(0, 0, BW, BH);

    var s = scaleVal;
    ctx.save();
    ctx.translate(BW / 2 + offsetX, BH / 2 + offsetY);
    ctx.scale(flipX * s * stretchX, s * stretchY);
    ctx.rotate(rotateVal);
    ctx.translate(-12, -12);

    /* Faint fill for shape body */
    ctx.fillStyle = 'rgba(200,200,200,0.08)';
    ctx.fill(fpPath);

    /* Main ridge stroke with breaks */
    ctx.setLineDash([dashA, dashB]);
    ctx.strokeStyle = 'rgba(200,200,200,0.7)';
    ctx.lineWidth = lineW / s;
    ctx.lineCap = 'round';
    ctx.lineJoin = 'round';
    ctx.stroke(fpPath);

    /* Thinner detail pass — offset dash for interleaved detail */
    ctx.setLineDash([dashA * 0.6, dashB * 1.2]);
    ctx.strokeStyle = 'rgba(180,180,180,0.35)';
    ctx.lineWidth = (lineW * 0.5) / s;
    ctx.stroke(fpPath);

    ctx.setLineDash([]);
    ctx.restore();

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
        scanX = 10 + Math.floor(rng() * (BW - 20));
        scanY = 10 + Math.floor(rng() * (BH - 20));
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
