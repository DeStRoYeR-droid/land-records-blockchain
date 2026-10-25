"use client";

import { Mark } from "@/components/ui/icons";
import { OrgBadge } from "@/components/ui/badges";
import { Hash } from "@/components/ui/hash";
import { Timestamp } from "@/components/ui/time";
import { LIFECYCLE, ROLE_TITLE, STATUS_INFO } from "@/lib/format";
import type { AppStatus, CheckResult, TrailEntry } from "@/lib/types";

const SEG: Record<string, string> = {
  reg: "var(--reg)",
  rev: "var(--rev)",
  col: "var(--col)",
  ok: "var(--field)",
  bad: "var(--red)",
  warn: "var(--ochre)",
  muted: "var(--ink-3)",
};

/** Where a file is on its way: Registration, Revenue, Collectorate. */
export function RouteBar({ status }: { status: AppStatus }) {
  const stopped = status === "REJECTED" || status === "FLAGGED";
  const at = LIFECYCLE.indexOf(status === "FLAGGED" ? "DOCS_VERIFIED" : status === "REJECTED" ? "WITH_COLLECTORATE" : status);
  return (
    <ol className="route" aria-label="Where this file is">
      {LIFECYCLE.map((s, i) => {
        const done = i < at || status === "APPROVED";
        const current = i === at && status !== "APPROVED";
        const state = current && stopped ? "stopped" : current ? "current" : done ? "done" : "todo";
        return (
          <li key={s} data-state={state} style={{ ["--seg" as string]: SEG[STATUS_INFO[s].tone] }} aria-current={current ? "step" : undefined}>
            {STATUS_INFO[s].label}
            <span className="visually-hidden">{state === "done" ? " (done)" : state === "current" ? " (now)" : state === "stopped" ? " (stopped here)" : ""}</span>
          </li>
        );
      })}
    </ol>
  );
}

/** Who did what, in order. The validator's lines are drawn dashed: evidence, not a decision. */
export function Trail({ entries }: { entries: TrailEntry[] }) {
  return (
    <ol className="trail">
      {entries.map((t, i) => (
        <li key={`${t.txId}-${i}`} data-org={t.org} data-agent={t.role === "agent" ? "true" : undefined}>
          <span className="dot" aria-hidden="true" />
          <span className="what">{t.action}</span>
          <span className="meta">
            <span className="who">
              {t.by} ({ROLE_TITLE[t.role] ?? t.role})
            </span>
            <OrgBadge msp={t.org} />
            <Timestamp value={t.at} />
            <Hash value={t.txId} head={8} tail={4} label="transaction" />
          </span>
        </li>
      ))}
    </ol>
  );
}

const CHECK_ORDER = ["fail", "warn", "skipped", "pass"];

export function ChecksList({ checks, showPassed = true }: { checks: CheckResult[]; showPassed?: boolean }) {
  const sorted = [...checks].sort((a, b) => CHECK_ORDER.indexOf(a.status) - CHECK_ORDER.indexOf(b.status));
  return (
    <ol className="checklist" aria-label="Checks the validator ran">
      {sorted
        .filter((c) => showPassed || c.status !== "pass")
        .map((c) => (
          <li key={c.id}>
            <Mark state={c.status} />
            <div>
              <div className="title">
                {c.label} <span className="visually-hidden">: {c.status}</span>
              </div>
              <div className="detail">{c.detail}</div>
              {c.evidence.length && c.status !== "pass" ? (
                <ul className="evidence">
                  {c.evidence.map((e, i) => (
                    <li key={i}>{e}</li>
                  ))}
                </ul>
              ) : null}
            </div>
          </li>
        ))}
    </ol>
  );
}

/** "0.97" with a word, for dashboards. */
export function Score({ value, label }: { value: number | null | undefined; label: string }) {
  if (value == null) return <span className="faint">—</span>;
  const tone = value >= 0.9 ? "ok" : value >= 0.8 ? "warn" : "bad";
  return (
    <span className="badge" data-tone={tone} title={`${label}: ${value.toFixed(3)}`}>
      {value.toFixed(2)}
    </span>
  );
}
