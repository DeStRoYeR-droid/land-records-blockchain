"use client";

import { useState } from "react";

import { chunk, shortHash } from "@/lib/format";

import { IconCopy, IconDone } from "./icons";

export function CopyButton({ value, label = "Copy" }: { value: string; label?: string }) {
  const [done, setDone] = useState(false);
  return (
    <button
      type="button"
      className="copy-btn"
      aria-label={done ? "Copied" : `${label}: ${value}`}
      title={done ? "Copied" : label}
      onClick={async () => {
        try {
          await navigator.clipboard.writeText(value);
        } catch {
          const ta = document.createElement("textarea");
          ta.value = value;
          document.body.appendChild(ta);
          ta.select();
          document.execCommand("copy");
          ta.remove();
        }
        setDone(true);
        setTimeout(() => setDone(false), 1400);
      }}
    >
      {done ? <IconDone /> : <IconCopy />}
    </button>
  );
}

/**
 * A hash, ID or key. Long values are shortened in the middle (the full value
 * is in the tooltip and the copy button); `full` shows it all in groups of four.
 */
export function Hash({
  value,
  full = false,
  copy = true,
  head = 10,
  tail = 6,
  label,
}: {
  value: string | null | undefined;
  full?: boolean;
  copy?: boolean;
  head?: number;
  tail?: number;
  label?: string;
}) {
  if (!value) return <span className="faint">—</span>;
  return (
    <span className="hash">
      <span className="hash-text" title={value} aria-label={label ? `${label} ${value}` : undefined}>
        {full ? chunk(value) : shortHash(value, head, tail)}
      </span>
      {copy ? <CopyButton value={value} label={label ? `Copy ${label}` : "Copy"} /> : null}
    </span>
  );
}

/** An OpenPGP fingerprint, as gpg prints it: ten groups of four. */
export function Fingerprint({ value }: { value: string }) {
  const groups = value.toUpperCase().replace(/\s+/g, "").match(/.{1,4}/g) ?? [];
  return (
    <span className="hash">
      <span className="fingerprint" title={value}>
        {groups.slice(0, 5).join(" ")}
        {"  "}
        {groups.slice(5).join(" ")}
      </span>
      <CopyButton value={value.toUpperCase()} label="Copy fingerprint" />
    </span>
  );
}
