/**
 * Server-Sent Events over fetch(), so the request can carry the bearer token
 * (EventSource cannot send headers) and can be a POST (the validator's Q&A).
 */

import { gwUrl, type Token } from "./api";

export interface StreamOptions {
  token?: Token;
  method?: "GET" | "POST";
  body?: unknown;
  signal: AbortSignal;
  onEvent: (data: Record<string, unknown>) => void;
  onOpen?: () => void;
}

export async function stream(path: string, { token, method = "GET", body, signal, onEvent, onOpen }: StreamOptions): Promise<void> {
  const headers: Record<string, string> = { Accept: "text/event-stream" };
  if (token) headers.Authorization = `Bearer ${token}`;
  if (body !== undefined) headers["Content-Type"] = "application/json";
  const resp = await fetch(gwUrl(path), {
    method,
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
    signal,
    cache: "no-store",
  });
  if (!resp.ok || !resp.body) {
    let message = `stream failed (${resp.status})`;
    try {
      message = ((await resp.json()) as { error?: string }).error || message;
    } catch {
      /* ignore */
    }
    throw new Error(message);
  }
  onOpen?.();
  const reader = resp.body.pipeThrough(new TextDecoderStream()).getReader();
  let buffer = "";
  let data: string[] = [];
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += value;
    let nl: number;
    while ((nl = buffer.indexOf("\n")) >= 0) {
      const line = buffer.slice(0, nl).replace(/\r$/, "");
      buffer = buffer.slice(nl + 1);
      if (line === "") {
        if (data.length) {
          try {
            onEvent(JSON.parse(data.join("\n")) as Record<string, unknown>);
          } catch {
            /* a frame that is not JSON: ignore */
          }
        }
        data = [];
      } else if (line.startsWith("data:")) {
        data.push(line.slice(5).trimStart());
      }
      // ":" comments are keep-alives; "event:" names are carried inside the JSON too.
    }
  }
}

/**
 * Keep a stream open: reconnect with backoff when it drops.
 * Returns a function that closes it.
 */
export function subscribe(
  path: string,
  opts: Omit<StreamOptions, "signal" | "method" | "body"> & { onStatus?: (s: "connecting" | "open" | "retrying") => void },
): () => void {
  const ctrl = new AbortController();
  let delay = 1000;
  const run = async () => {
    while (!ctrl.signal.aborted) {
      opts.onStatus?.("connecting");
      try {
        await stream(path, {
          ...opts,
          signal: ctrl.signal,
          onOpen: () => {
            delay = 1000;
            opts.onStatus?.("open");
            opts.onOpen?.();
          },
        });
      } catch {
        /* fall through to retry */
      }
      if (ctrl.signal.aborted) return;
      opts.onStatus?.("retrying");
      await new Promise((r) => setTimeout(r, delay));
      delay = Math.min(delay * 2, 15000);
    }
  };
  void run();
  return () => ctrl.abort();
}
