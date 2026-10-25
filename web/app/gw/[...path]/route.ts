/**
 * /gw/* -> the LandChain gateway (python -m gateway).
 *
 * The browser only ever talks to this site; this handler forwards the call,
 * with the caller's bearer token, and streams the answer back -- including
 * the Server-Sent Event feeds (live blocks, the validator's live trace).
 */

import type { NextRequest } from "next/server";

import { GATEWAY_URL } from "@/lib/server";

export const dynamic = "force-dynamic";

const FORWARD_REQUEST = ["authorization", "content-type", "accept"];
const FORWARD_RESPONSE = ["content-type", "content-disposition", "cache-control"];

async function forward(req: NextRequest, ctx: { params: Promise<{ path: string[] }> }) {
  const { path } = await ctx.params;
  if (!path.length || (path[0] !== "api" && path[0] !== "health")) {
    return Response.json({ error: "Not a gateway API path" }, { status: 404 });
  }
  const target = `${GATEWAY_URL}/${path.map(encodeURIComponent).join("/")}${req.nextUrl.search}`;
  const headers = new Headers();
  for (const name of FORWARD_REQUEST) {
    const v = req.headers.get(name);
    if (v) headers.set(name, v);
  }
  const wantsStream = (req.headers.get("accept") || "").includes("text/event-stream");
  const body = req.method === "GET" || req.method === "HEAD" ? undefined : await req.arrayBuffer();

  let upstream: Response;
  try {
    upstream = await fetch(target, {
      method: req.method,
      headers,
      body,
      cache: "no-store",
      redirect: "manual",
      // Streams stay open until the browser goes away; plain calls get a deadline.
      signal: wantsStream ? req.signal : AbortSignal.any([req.signal, AbortSignal.timeout(90_000)]),
    });
  } catch (err) {
    if ((err as Error).name === "AbortError" && req.signal.aborted) return new Response(null, { status: 499 });
    return Response.json(
      { error: `The LandChain gateway at ${GATEWAY_URL} is not reachable. Start the network with ./startup.sh.` },
      { status: 503 },
    );
  }

  const out = new Headers();
  for (const name of FORWARD_RESPONSE) {
    const v = upstream.headers.get(name);
    if (v) out.set(name, v);
  }
  if ((upstream.headers.get("content-type") || "").includes("text/event-stream")) {
    // no-transform stops the production server from gzip-buffering the stream
    out.set("cache-control", "no-cache, no-transform");
    out.set("x-accel-buffering", "no");
  } else if (!out.has("cache-control")) {
    out.set("cache-control", "no-store");
  }
  return new Response(upstream.body, { status: upstream.status, headers: out });
}

export { forward as GET, forward as POST };
