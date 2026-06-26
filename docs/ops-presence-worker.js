export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const corsHeaders = {
      'Access-Control-Allow-Origin': '*',
      'Access-Control-Allow-Methods': 'GET, POST, DELETE, OPTIONS',
      'Access-Control-Allow-Headers': 'Content-Type',
    };

    if (request.method === 'OPTIONS') {
      return new Response(null, { headers: corsHeaders });
    }

    /* ── Presence routes ─────────────────────────────── */
    if (url.pathname === '/presence') {
      if (request.method === 'POST') {
        const ip = request.headers.get('CF-Connecting-IP') || 'unknown';
        const recent = await env.OPS_PRESENCE.get('ratelimit:' + ip);
        if (recent) {
          return new Response('Too fast', { status: 429, headers: corsHeaders });
        }
        await env.OPS_PRESENCE.put('ratelimit:' + ip, '1', { expirationTtl: 10 });

        const { callsign } = await request.json();
        if (!callsign || typeof callsign !== 'string' || callsign.length > 40) {
          return new Response('Invalid callsign', { status: 400, headers: corsHeaders });
        }
        // Single-blob: read all operators, update one, write back
        let blob = { operators: {} };
        const raw = await env.OPS_PRESENCE.get('_operators', 'text');
        if (raw) {
          try { blob = JSON.parse(raw); } catch {}
        }
        blob.operators[callsign] = { lastSeen: Date.now() };
        await env.OPS_PRESENCE.put('_operators', JSON.stringify(blob), { expirationTtl: 120 });
        return new Response('OK', { headers: corsHeaders });
      }

      if (request.method === 'GET') {
        const raw = await env.OPS_PRESENCE.get('_operators', 'text');
        const operators = [];
        if (raw) {
          try {
            const blob = JSON.parse(raw);
            for (const [callsign, data] of Object.entries(blob.operators || {})) {
              operators.push({ callsign, lastSeen: data.lastSeen });
            }
          } catch {}
        }
        return new Response(JSON.stringify({ operators, count: operators.length }), {
          headers: { 'Content-Type': 'application/json', ...corsHeaders },
        });
      }

      if (request.method === 'DELETE') {
        const { callsign } = await request.json();
        if (callsign) {
          let blob = { operators: {} };
          const raw = await env.OPS_PRESENCE.get('_operators', 'text');
          if (raw) {
            try { blob = JSON.parse(raw); } catch {}
          }
          delete blob.operators[callsign];
          const keys = Object.keys(blob.operators);
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
      const recent = await env.OPS_PRESENCE.get('ratelimit:resp:' + ip);
      if (recent) {
        return new Response('Too fast', { status: 429, headers: corsHeaders });
      }
      await env.OPS_PRESENCE.put('ratelimit:resp:' + ip, '1', { expirationTtl: 5 });

      const body = await request.json();
      if (!body || !body.answer) {
        return new Response('Missing answer', { status: 400, headers: corsHeaders });
      }
      const ts = Date.now();
      const callsign = (body.callsign || 'anon').slice(0, 40);
      // Responses stored separately — one KV entry per response, infrequent writes
      const key = 'response:' + ts + ':' + callsign;
      await env.OPS_PRESENCE.put(key, JSON.stringify({
        callsign,
        question: (body.question || '').slice(0, 500),
        answer: (body.answer || '').slice(0, 2000),
        timestamp: ts,
      }));
      return new Response('OK', { headers: corsHeaders });
    }

    /* ── Response list route ─────────────────────────── */
    if (url.pathname === '/responses' && request.method === 'GET') {
      const search = (url.searchParams.get('search') || '').toLowerCase();
      const sort = url.searchParams.get('sort') || 'timestamp';
      const order = url.searchParams.get('order') || 'desc';

      const list = await env.OPS_PRESENCE.list({ prefix: 'response:' });
      let responses = [];
      for (const key of list.keys) {
        const raw = await env.OPS_PRESENCE.get(key.name, 'text');
        if (raw) {
          try {
            const val = JSON.parse(raw);
            if (search && !val.callsign.toLowerCase().includes(search) &&
                !val.question.toLowerCase().includes(search) &&
                !val.answer.toLowerCase().includes(search)) {
              continue;
            }
            responses.push(val);
          } catch {}
        }
      }

      responses.sort((a, b) => {
        if (sort === 'callsign') {
          return order === 'asc'
            ? (a.callsign || '').localeCompare(b.callsign || '')
            : (b.callsign || '').localeCompare(a.callsign || '');
        }
        return order === 'asc'
          ? (a.timestamp || 0) - (b.timestamp || 0)
          : (b.timestamp || 0) - (a.timestamp || 0);
      });

      return new Response(JSON.stringify({ responses, count: responses.length }), {
        headers: { 'Content-Type': 'application/json', ...corsHeaders },
      });
    }

    return new Response('Not found', { status: 404, headers: corsHeaders });
  },
};
