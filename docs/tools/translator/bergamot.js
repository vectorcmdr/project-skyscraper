import { LatencyOptimisedTranslator, SupersededError, CancelledError, TranslatorBacking } from './translator.js';

if (!('DecompressionStream' in self)) {
  throw new Error('Your browser does not support on-device translation. Please use Chrome 80+, Firefox 110+, or Safari 16.4+.');
}

class DecompressingBacking extends TranslatorBacking {
  async fetch(url, checksum, extra) {
    var buffer = await super.fetch(url, checksum, extra);
    if (!url.endsWith('.gz')) return buffer;
    var stream = new Response(buffer).body.pipeThrough(new DecompressionStream('gzip'));
    return new Response(stream).arrayBuffer();
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
  try {
    var t = await getTranslator();
    await t.translate({ from: 'fr', to: 'en', text: 'bonjour', html: false });
    setStatus('Translation module ready', 'is-ready');
  } catch (e) {
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
