export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const corsHeaders = {
      'Access-Control-Allow-Origin': '*',
      'Access-Control-Allow-Methods': 'GET, POST, DELETE, OPTIONS',
      'Access-Control-Allow-Headers': 'Content-Type',
    };

    // Global killswitch
    const killed = await env.OPS_PRESENCE.get('kill');
    if (killed !== null) {
      return new Response('Service disabled', { status: 503, headers: corsHeaders });
    }

    // Auto-shutdown: count writes and kill at 900k (90% of 1M monthly)
    async function countWrite() {
      var today = new Date().toISOString().slice(0, 10);
      var key = 'writes:' + today;
      var raw = await env.OPS_PRESENCE.get(key, { type: 'text' });
      var count = raw ? parseInt(raw, 10) : 0;
      if (isNaN(count) || count < 0) count = 0;
      count++;
      if (count >= 900000) {
        await env.OPS_PRESENCE.put('kill', '1');
        return false;
      }
      await env.OPS_PRESENCE.put(key, String(count), { expirationTtl: 86400 * 35 });
      return true;
    }

    if (request.method === 'OPTIONS') {
      return new Response(null, { headers: corsHeaders });
    }

    /* ── Presence routes ─────────────────────────────── */
    if (url.pathname === '/presence') {
      if (request.method === 'POST') {
        try {
          var data = await request.json();
          var callsign = (data && data.callsign) || '';

          var ip = request.headers.get('CF-Connecting-IP') || 'unknown';
          var exempt = ['vector_cmdr'];
          if (!exempt.includes(callsign)) {
            var recent = await env.OPS_PRESENCE.get('ratelimit:' + ip);
            if (recent) {
              return new Response('Too fast', { status: 429, headers: corsHeaders });
            }
            await env.OPS_PRESENCE.put('ratelimit:' + ip, '1', { expirationTtl: 60 });
          }

          if (!(await countWrite())) {
            return new Response('Service disabled', { status: 503, headers: corsHeaders });
          }

          if (!callsign || typeof callsign !== 'string' || callsign.length > 40) {
            return new Response('Invalid callsign', { status: 400, headers: corsHeaders });
          }
          var blob = { operators: {} };
          var raw = await env.OPS_PRESENCE.get('_operators', { type: 'text' });
          if (raw) { try { blob = JSON.parse(raw); } catch {} }
          blob.operators[callsign] = { lastSeen: Date.now() };
          await env.OPS_PRESENCE.put('_operators', JSON.stringify(blob), { expirationTtl: 120 });
          return new Response('OK', { headers: corsHeaders });
        } catch (err) {
          return new Response('Error: ' + err.message, { status: 500, headers: corsHeaders });
        }
      }

      if (request.method === 'GET') {
        var raw = await env.OPS_PRESENCE.get('_operators', { type: 'text' });
        var operators = [];
        if (raw) {
          try {
            var blob = JSON.parse(raw);
            for (var callsign in blob.operators || {}) {
              operators.push({ callsign: callsign, lastSeen: blob.operators[callsign].lastSeen });
            }
          } catch {}
        }
        return new Response(JSON.stringify({ operators: operators, count: operators.length }), {
          headers: { 'Content-Type': 'application/json', ...corsHeaders },
        });
      }

      if (request.method === 'DELETE') {
        var { callsign } = await request.json();
        if (callsign) {
          var blob = { operators: {} };
          var raw = await env.OPS_PRESENCE.get('_operators', { type: 'text' });
          if (raw) { try { blob = JSON.parse(raw); } catch {} }
          delete blob.operators[callsign];
          var keys = Object.keys(blob.operators);
          if (keys.length > 0) {
            await env.OPS_PRESENCE.put('_operators', JSON.stringify(blob), { expirationTtl: 120 });
          } else {
            await env.OPS_PRESENCE.delete('_operators');
          }
        }
        return new Response('OK', { headers: corsHeaders });
      }
    }

    /* ── Response capture route ──────────────────────── */
    if (url.pathname === '/response' && request.method === 'POST') {
      const ip = request.headers.get('CF-Connecting-IP') || 'unknown';
      var recent = await env.OPS_PRESENCE.get('ratelimit:resp:' + ip);
      if (recent) {
        return new Response('Too fast', { status: 429, headers: corsHeaders });
      }
      await env.OPS_PRESENCE.put('ratelimit:resp:' + ip, '1', { expirationTtl: 60 });

      if (!(await countWrite())) {
        return new Response('Service disabled', { status: 503, headers: corsHeaders });
      }

      var body = await request.json();
      if (!body || !body.answer) {
        return new Response('Missing answer', { status: 400, headers: corsHeaders });
      }
      var ts = Date.now();
      var callsign = (body.callsign || 'anon').slice(0, 40);
      var key = 'response:' + ts + ':' + callsign;
      await env.OPS_PRESENCE.put(key, JSON.stringify({
        callsign: callsign,
        question: (body.question || '').slice(0, 500),
        answer: (body.answer || '').slice(0, 2000),
        timestamp: ts,
      }));
      return new Response('OK', { headers: corsHeaders });
    }

    /* ── Response list route ─────────────────────────── */
    if (url.pathname === '/responses' && request.method === 'GET') {
      var search = (url.searchParams.get('search') || '').toLowerCase();
      var sort = url.searchParams.get('sort') || 'timestamp';
      var order = url.searchParams.get('order') || 'desc';

      var list = await env.OPS_PRESENCE.list({ prefix: 'response:' });
      var responses = [];
      for (var key of list.keys) {
        var raw = await env.OPS_PRESENCE.get(key.name, { type: 'text' });
        if (raw) {
          try {
            var val = JSON.parse(raw);
            if (search && !val.callsign.toLowerCase().includes(search) &&
                !val.question.toLowerCase().includes(search) &&
                !val.answer.toLowerCase().includes(search)) {
              continue;
            }
            responses.push(val);
          } catch {}
        }
      }

      responses.sort(function (a, b) {
        if (sort === 'callsign') {
          return order === 'asc'
            ? (a.callsign || '').localeCompare(b.callsign || '')
            : (b.callsign || '').localeCompare(a.callsign || '');
        }
        return order === 'asc'
          ? (a.timestamp || 0) - (b.timestamp || 0)
          : (b.timestamp || 0) - (a.timestamp || 0);
      });

      return new Response(JSON.stringify({ responses: responses, count: responses.length }), {
        headers: { 'Content-Type': 'application/json', ...corsHeaders },
      });
    }

    return new Response('Not found', { status: 404, headers: corsHeaders });
  },
};
