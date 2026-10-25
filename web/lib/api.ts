/**
 * Browser -> gateway calls. Everything goes through /gw on this site
 * (app/gw/[...path]/route.ts forwards to the gateway), with the session's
 * bearer token.
 */

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }

  /** The gateway or the network itself is down, as opposed to a refused request. */
  get offline(): boolean {
    return this.status === 0 || this.status === 502 || this.status === 503 || this.status === 504;
  }
}

export type Token = string | null | undefined;

export function gwUrl(path: string): string {
  return `/gw${path.startsWith("/") ? path : `/${path}`}`;
}

export async function api<T>(
  path: string,
  { token, method = "GET", body, signal }: { token?: Token; method?: string; body?: unknown; signal?: AbortSignal } = {},
): Promise<T> {
  const headers: Record<string, string> = { Accept: "application/json" };
  if (token) headers.Authorization = `Bearer ${token}`;
  if (body !== undefined) headers["Content-Type"] = "application/json";
  let resp: Response;
  try {
    resp = await fetch(gwUrl(path), {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
      signal,
      cache: "no-store",
    });
  } catch (err) {
    if ((err as Error).name === "AbortError") throw err;
    throw new ApiError(0, "This site could not be reached. Check that the web app is still running.");
  }
  const text = await resp.text();
  let data: unknown = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = text;
  }
  if (!resp.ok) {
    const fromBody =
      data && typeof data === "object" && "error" in data ? String((data as { error: unknown }).error) : "";
    const message = fromBody || `Request failed (${resp.status})`;
    if (resp.status === 401 && typeof window !== "undefined") {
      window.dispatchEvent(new CustomEvent("landchain:unauthorized", { detail: { path, token } }));
    }
    throw new ApiError(resp.status, message);
  }
  return data as T;
}

/** Fetch raw bytes (report files) with the session token. */
export async function apiBytes(path: string, token?: Token): Promise<Uint8Array> {
  const resp = await fetch(gwUrl(path), { headers: token ? { Authorization: `Bearer ${token}` } : {}, cache: "no-store" });
  if (!resp.ok) {
    let message = `Download failed (${resp.status})`;
    try {
      message = ((await resp.json()) as { error?: string }).error || message;
    } catch {
      /* ignore */
    }
    throw new ApiError(resp.status, message);
  }
  return new Uint8Array(await resp.arrayBuffer());
}

export function errorMessage(err: unknown): string {
  if (err instanceof ApiError) return err.message;
  if (err instanceof Error) return err.message;
  return String(err);
}

/** Save bytes or text as a file in the browser's downloads. */
export function download(name: string, data: Uint8Array | string, type = "application/octet-stream"): void {
  const part: BlobPart = typeof data === "string" ? data : new Uint8Array(data);
  const url = URL.createObjectURL(new Blob([part], { type }));
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
