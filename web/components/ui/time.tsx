"use client";

import { useEffect, useState } from "react";

import { ago, toDate, when } from "@/lib/format";

/** "3 min ago", kept fresh; the exact IST time is in the tooltip. */
export function TimeAgo({ value }: { value: string | number | null | undefined }) {
  const [now, setNow] = useState<number | null>(null);
  useEffect(() => {
    setNow(Date.now());
    const id = setInterval(() => setNow(Date.now()), 30_000);
    return () => clearInterval(id);
  }, []);
  const d = toDate(value);
  if (!d) return <span className="faint">—</span>;
  return (
    <time dateTime={d.toISOString()} title={when(value)}>
      {now === null ? when(value) : ago(value, now)}
    </time>
  );
}

/** The exact time, in IST. */
export function Timestamp({ value }: { value: string | number | null | undefined }) {
  const d = toDate(value);
  if (!d) return <span className="faint">—</span>;
  return <time dateTime={d.toISOString()}>{when(value)}</time>;
}
