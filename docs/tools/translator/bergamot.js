import { LatencyOptimisedTranslator, SupersededError, CancelledError, TranslatorBacking } from './translator.js';

if (!('DecompressionStream' in self)) {
  throw new Error('Your browser does not support on-device translation. Please use Chrome 80+, Firefox 110+, or Safari 16.4+.');
}

class DecompressingBacking extends TranslatorBacking {
  async fetch(url, checksum, extra) {
    var controller = new AbortController();
    var abort = function() { controller.abort(); };
    var timeout = this.downloadTimeout ? setTimeout(abort, this.downloadTimeout) : null;
    var signal = controller.signal;

    if (extra && extra.signal) {
      extra.signal.addEventListener('abort', abort);
    }

    try {
      var options = { credentials: 'omit', signal: signal };
      if (checksum) {
        options.integrity = 'sha256-' + this.hexToBase64(checksum);
      }
      if (typeof window === 'undefined') {
        delete options.integrity;
      }

      var response = await fetch(url, options);
      var contentLength = parseInt(response.headers.get('Content-Length') || '0', 10);

      var reader = response.body.getReader();
      var chunks = [];
      var received = 0;

      while (true) {
        var result = await reader.read();
        if (result.done) break;
        chunks.push(result.value);
        received += result.value.byteLength;
        if (contentLength > 0 && window._addDownloadProgress) {
          window._addDownloadProgress(result.value.byteLength);
        }
      }

      var totalLength = 0;
      for (var i = 0; i < chunks.length; i++) {
        totalLength += chunks[i].byteLength;
      }
      var combined = new Uint8Array(totalLength);
      var pos = 0;
      for (var i = 0; i < chunks.length; i++) {
        combined.set(chunks[i], pos);
        pos += chunks[i].byteLength;
      }

      var buffer = combined.buffer;

      if (url.endsWith('.gz')) {
        var ds = new Response(buffer).body.pipeThrough(new DecompressionStream('gzip'));
        return new Response(ds).arrayBuffer();
      }
      return buffer;

    } finally {
      if (timeout) clearTimeout(timeout);
      if (extra && extra.signal) extra.signal.removeEventListener('abort', abort);
    }
  }
}

var translator = null;
var initPromise = null;

async function getTranslator() {
  if (translator) return translator;
  if (initPromise) return initPromise;

  initPromise = (async function() {
    var backing = new DecompressingBacking({
      downloadTimeout: 120000,
      registryUrl: 'translator/models/index.json'
    });
    var t = new LatencyOptimisedTranslator({}, backing);
    await t.worker;
    return t;
  })();

  try {
    translator = await initPromise;
    return translator;
  } catch (e) {
    initPromise = null;
    throw e;
  }
}

window.translateText = async function(text, from, to) {
  var t = await getTranslator();
  var response = await t.translate({ from: from, to: to, text: text, html: false });
  return response.target.text;
};

/* ── Download progress bar (accumulated across all model files) ── */

var _dlTotal = 0;
var _dlReceived = 0;

window._initDownloadProgress = function(totalBytes) {
  _dlTotal = totalBytes;
  _dlReceived = 0;
  var bar = document.getElementById('downloadProgressBar');
  var text = document.getElementById('downloadProgressText');
  var container = document.getElementById('downloadProgress');
  if (bar) bar.style.width = '0%';
  if (text) text.textContent = '0%';
  if (container) container.style.display = 'flex';
};

window._addDownloadProgress = function(delta) {
  if (_dlTotal <= 0) return;
  _dlReceived += delta;
  var pct = Math.round(_dlReceived / _dlTotal * 100);
  if (pct > 100) pct = 100;
  var bar = document.getElementById('downloadProgressBar');
  var text = document.getElementById('downloadProgressText');
  if (bar) bar.style.width = pct + '%';
  if (text) text.textContent = pct + '%';
};

window._showDownloadProgress = function() {
  _dlTotal = 26324715;
  _dlReceived = 0;
  var bar = document.getElementById('downloadProgressBar');
  var text = document.getElementById('downloadProgressText');
  var container = document.getElementById('downloadProgress');
  if (bar) bar.style.width = '0%';
  if (text) text.textContent = '0%';
  if (container) container.style.display = 'flex';
};

window._hideDownloadProgress = function() {
  var container = document.getElementById('downloadProgress');
  if (container) container.style.display = 'none';
};

/* ── Consent dialog ─────────────────────────────────────── */

var consent = localStorage.getItem('translation_consent');

function showConsent() {
  var el = document.getElementById('translationConsent');
  if (el) el.style.display = 'flex';
}

function hideConsent() {
  var el = document.getElementById('translationConsent');
  if (el) el.style.display = 'none';
}

function setStatus(msg, className) {
  var el = document.getElementById('translationStatus');
  if (!el) return;
  el.textContent = msg;
  el.style.display = 'block';
  el.className = 'translation-status' + (className ? ' ' + className : '');
}

window.acceptTranslationDownload = async function() {
  localStorage.setItem('translation_consent', 'yes');
  hideConsent();
  setStatus('Downloading translation module (~26 MB)...');
  window._initDownloadProgress(26324715);
  try {
    var t = await getTranslator();
    await t.translate({ from: 'fr', to: 'en', text: 'bonjour', html: false });
    window._hideDownloadProgress();
    setStatus('Translation module ready', 'is-ready');
  } catch (e) {
    window._hideDownloadProgress();
    setStatus('Download failed: ' + e.message + '. Translation will still work on first use.', 'is-error');
  }
};

window.declineTranslationDownload = function() {
  localStorage.setItem('translation_consent', 'no');
  hideConsent();
};

if (!consent) {
  document.addEventListener('DOMContentLoaded', showConsent);
}
