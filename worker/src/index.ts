/**
 * AIMS Console Sync Worker
 * Cloudflare Worker — KV-backed incident sharing with 6-char share codes.
 */

export interface Env {
  INCIDENTS: KVNamespace;
  CORS_ORIGIN: string;
}

// Unambiguous uppercase characters (no O, 0, I, 1, L)
const SHARE_CHARS = "ABCDEFGHJKMNPQRSTUVWXYZ23456789";
const CODE_LENGTH = 6;
const TTL_SECONDS = 30 * 24 * 60 * 60; // 30 days

function generateShareCode(): string {
  const bytes = new Uint8Array(CODE_LENGTH);
  crypto.getRandomValues(bytes);
  return Array.from(bytes)
    .map((b) => SHARE_CHARS[b % SHARE_CHARS.length])
    .join("");
}

function corsHeaders(origin: string): Record<string, string> {
  return {
    "Access-Control-Allow-Origin": origin,
    "Access-Control-Allow-Methods": "GET, POST, PUT, DELETE, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type",
    "Access-Control-Max-Age": "86400",
  };
}

function jsonResponse(
  body: unknown,
  status: number,
  origin: string
): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: {
      "Content-Type": "application/json",
      ...corsHeaders(origin),
    },
  });
}

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const origin = env.CORS_ORIGIN || "*";
    const method = request.method.toUpperCase();
    const url = new URL(request.url);
    const path = url.pathname;

    // Handle CORS preflight
    if (method === "OPTIONS") {
      return new Response(null, {
        status: 204,
        headers: corsHeaders(origin),
      });
    }

    // POST /api/incidents — upload new incident, get share code
    if (method === "POST" && path === "/api/incidents") {
      let body: unknown;
      try {
        body = await request.json();
      } catch {
        return jsonResponse({ error: "Invalid JSON body" }, 400, origin);
      }
      const shareCode = generateShareCode();
      const key = `incident:${shareCode}`;
      await env.INCIDENTS.put(key, JSON.stringify(body), {
        expirationTtl: TTL_SECONDS,
      });
      return jsonResponse({ shareCode }, 201, origin);
    }

    // Match /api/incidents/:shareCode
    const incidentMatch = path.match(/^\/api\/incidents\/([A-Z0-9]{4,12})$/i);
    if (incidentMatch) {
      const shareCode = incidentMatch[1].toUpperCase();
      const key = `incident:${shareCode}`;

      // GET — fetch incident
      if (method === "GET") {
        const value = await env.INCIDENTS.get(key);
        if (value === null) {
          return jsonResponse({ error: "Incident not found" }, 404, origin);
        }
        return new Response(value, {
          status: 200,
          headers: {
            "Content-Type": "application/json",
            ...corsHeaders(origin),
          },
        });
      }

      // PUT — replace incident blob
      if (method === "PUT") {
        let body: unknown;
        try {
          body = await request.json();
        } catch {
          return jsonResponse({ error: "Invalid JSON body" }, 400, origin);
        }
        await env.INCIDENTS.put(key, JSON.stringify(body), {
          expirationTtl: TTL_SECONDS,
        });
        return jsonResponse({ ok: true }, 200, origin);
      }

      // DELETE — remove incident
      if (method === "DELETE") {
        await env.INCIDENTS.delete(key);
        return jsonResponse({ ok: true }, 200, origin);
      }
    }

    return jsonResponse({ error: "Not found" }, 404, origin);
  },
};
