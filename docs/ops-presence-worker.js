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
        const { callsign } = await request.json();
        if (!callsign || typeof callsign !== 'string' || callsign.length > 40) {
          return new Response('Invalid callsign', { status: 400, headers: corsHeaders });
        }
        await env.OPS_PRESENCE.put(callsign, JSON.stringify({ lastSeen: Date.now() }), { expirationTtl: 65 });
        return new Response('OK', { headers: corsHeaders });
      }

      if (request.method === 'GET') {
        const list = await env.OPS_PRESENCE.list();
        const operators = [];
        for (const key of list.keys) {
          if (key.name.startsWith('response:')) continue;
          const raw = await env.OPS_PRESENCE.get(key.name);
          if (raw) {
            try {
              const val = JSON.parse(raw);
              operators.push({ callsign: key.name, lastSeen: val.lastSeen });
            } catch { /* skip corrupt entries */ }
          }
        }
        const body = JSON.stringify({ operators, count: operators.length });
        return new Response(body, {
          headers: { 'Content-Type': 'application/json', ...corsHeaders },
        });
      }

      if (request.method === 'DELETE') {
        const { callsign } = await request.json();
        if (callsign) await env.OPS_PRESENCE.delete(callsign);
        return new Response('OK', { headers: corsHeaders });
      }
    }

    /* ── Response capture route ──────────────────────── */
    if (url.pathname === '/response' && request.method === 'POST') {
      const body = await request.json();
      if (!body || !body.answer) {
        return new Response('Missing answer', { status: 400, headers: corsHeaders });
      }
      const ts = Date.now();
      const callsign = (body.callsign || 'anon').slice(0, 40);
      const key = `response:${ts}:${callsign}`;
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
        const raw = await env.OPS_PRESENCE.get(key.name);
        if (raw) {
          try {
            const val = JSON.parse(raw);
            if (search && !val.callsign.toLowerCase().includes(search) &&
                !val.question.toLowerCase().includes(search) &&
                !val.answer.toLowerCase().includes(search)) {
              continue;
            }
            responses.push(val);
          } catch { /* skip corrupt entries */ }
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
