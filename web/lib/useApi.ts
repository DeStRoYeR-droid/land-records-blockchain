"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError, api, type Token } from "./api";

export interface ApiState<T> {
  data: T | null;
  error: ApiError | null;
  /** True only until the first answer arrives (later refreshes keep the old data on screen). */
  loading: boolean;
  reload: () => void;
}

/**
 * GET a gateway path and keep the result. `path = null` waits (e.g. until the
 * session is known). `refreshMs` polls; the page keeps showing the last good
 * data while a refresh is in flight.
 */
export function useApi<T>(path: string | null, token?: Token, { refreshMs }: { refreshMs?: number } = {}): ApiState<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [loading, setLoading] = useState(true);
  const [tick, setTick] = useState(0);
  const lastPath = useRef<string | null>(null);

  useEffect(() => {
    if (!path) return;
    if (lastPath.current !== path) {
      lastPath.current = path;
      setLoading(true);
      setData(null);
      setError(null);
    }
    const ctrl = new AbortController();
    api<T>(path, { token, signal: ctrl.signal })
      .then((d) => {
        setData(d);
        setError(null);
      })
      .catch((e: unknown) => {
        if ((e as Error).name === "AbortError") return;
        setError(e instanceof ApiError ? e : new ApiError(0, String(e)));
      })
      .finally(() => {
        if (!ctrl.signal.aborted) setLoading(false);
      });
    return () => ctrl.abort();
  }, [path, token, tick]);

  useEffect(() => {
    if (!path || !refreshMs) return;
    const id = setInterval(() => {
      if (document.visibilityState === "visible") setTick((t) => t + 1);
    }, refreshMs);
    return () => clearInterval(id);
  }, [path, refreshMs]);

  const reload = useCallback(() => setTick((t) => t + 1), []);
  return { data, error, loading, reload };
}

/** Run one action (a button press) and remember whether it is busy or failed. */
export function useAction<A extends unknown[], R>(fn: (...args: A) => Promise<R>) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<R | null>(null);
  const run = useCallback(
    async (...args: A): Promise<R | null> => {
      setBusy(true);
      setError(null);
      try {
        const r = await fn(...args);
        setResult(r);
        return r;
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
        return null;
      } finally {
        setBusy(false);
      }
    },
    [fn],
  );
  return { run, busy, error, result, setError, reset: () => (setError(null), setResult(null)) };
}
