/** Health check for ./landchain.sh status (and any load balancer). */

import { GATEWAY_URL } from "@/lib/server";

export const dynamic = "force-dynamic";

export async function GET() {
  let gateway = "down";
  try {
    const r = await fetch(`${GATEWAY_URL}/health`, { cache: "no-store", signal: AbortSignal.timeout(1500) });
    gateway = r.ok ? "up" : `http ${r.status}`;
  } catch {
    /* the web app is healthy even when the gateway is not */
  }
  return Response.json({ status: "ok", service: "web", gateway, gatewayUrl: GATEWAY_URL });
}
