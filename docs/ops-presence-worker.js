export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (url.pathname !== '/presence') {
      return new Response('Not found', { status: 404 });
    }

    // CORS headers for cross-origin requests from GitHub Pages
    const corsHeaders = {
      'Access-Control-Allow-Origin': '*',
      'Access-Control-Allow-Methods': 'GET, POST, DELETE, OPTIONS',
      'Access-Control-Allow-Headers': 'Content-Type',
    };

    if (request.method === 'OPTIONS') {
      return new Response(null, { headers: corsHeaders });
    }

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

    return new Response('Method not allowed', { status: 405, headers: corsHeaders });
  },
};
