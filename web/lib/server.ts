import "server-only";

/**
 * Server-side access to the gateway, for the public pages that render on the
 * server (landing page, land register, sitemap, share images).
 */

export const GATEWAY_URL = (process.env.LANDCHAIN_GATEWAY_URL || "http://127.0.0.1:13900").replace(/\/$/, "");

export class GatewayError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

export async function gw<T>(path: string, timeoutMs = 5000): Promise<T> {
  let resp: Response;
  try {
    resp = await fetch(`${GATEWAY_URL}${path}`, { cache: "no-store", signal: AbortSignal.timeout(timeoutMs) });
  } catch {
    throw new GatewayError(503, `The LandChain gateway at ${GATEWAY_URL} did not answer.`);
  }
  if (!resp.ok) {
    let message = `${resp.status} ${resp.statusText}`;
    try {
      const body = (await resp.json()) as { error?: string };
      if (body.error) message = body.error;
    } catch {
      /* not JSON */
    }
    throw new GatewayError(resp.status, message);
  }
  return (await resp.json()) as T;
}

/** Like gw(), but resolves to null when the gateway is down or the item is missing. */
export async function gwMaybe<T>(path: string, timeoutMs = 5000): Promise<T | null> {
  try {
    return await gw<T>(path, timeoutMs);
  } catch {
    return null;
  }
}
