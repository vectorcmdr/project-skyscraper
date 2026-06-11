import { LatencyOptimisedTranslator, SupersededError, CancelledError } from './translator.js';

var translator = null;
var initPromise = null;

async function getTranslator() {
  if (translator) return translator;
  if (initPromise) return initPromise;

  initPromise = (async function() {
    var t = new LatencyOptimisedTranslator({
      downloadTimeout: 120000,
      onerror: function(err) { console.error('[bergamot]', err); }
    });
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
