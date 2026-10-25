"use client";

import Link from "next/link";

import { RunDetail, RunStatusBadge } from "@/components/agent/run-detail";
import { VerdictBadge } from "@/components/ui/badges";
import { Loading, SkeletonPanel } from "@/components/ui/skeleton";
import { TimeAgo } from "@/components/ui/time";
import { api } from "@/lib/api";
import type { AgentRun } from "@/lib/types";
import { useAction, useApi } from "@/lib/useApi";
import { ErrorState } from "@/components/ui/states";

type RunRow = Omit<AgentRun, "steps" | "checks"> & { stepCount?: number };

/** The validator's evidence on one application: the latest run in full, earlier runs as a list. */
export function ValidatorPanel({ runs, token, canRevalidate, appId, onChange }: { runs: RunRow[]; token: string; canRevalidate: boolean; appId: string; onChange: () => void }) {
  const latest = runs.find((r) => r.status === "anchored") ?? runs[0];
  const detail = useApi<AgentRun>(latest ? `/api/agent/runs/${latest.runId}` : null, token, { refreshMs: latest && ["queued", "running"].includes(latest.status) ? 2500 : undefined });
  const re = useAction(async () => api(`/api/applications/${appId}/revalidate`, { token, method: "POST" }));
  return (
    <section className="stack" aria-labelledby="h-validator">
      <div className="panel-head" style={{ marginBottom: 0 }}>
        <h2 id="h-validator" style={{ fontSize: "var(--step-2)" }}>
          AI validator’s evidence
        </h2>
        {canRevalidate ? (
          <button type="button" className="btn" disabled={re.busy} onClick={() => void re.run().then((r) => r && setTimeout(onChange, 800))}>
            {re.busy ? <span className="spinner" aria-hidden="true" /> : null}
            Validate again
          </button>
        ) : null}
      </div>
      <p className="callout" data-tone="col">
        This is evidence for you to weigh. The validator cannot approve, reject or change this file; it can only anchor its own signed report.
      </p>
      {re.error ? (
        <p className="error-text" role="alert">
          {re.error}
        </p>
      ) : null}
      {!latest ? (
        <p className="muted">The validator has not looked at this file yet. It runs when the file reaches Revenue and the Collectorate.</p>
      ) : detail.error && !detail.data ? (
        <ErrorState error={detail.error} onRetry={detail.reload} />
      ) : !detail.data ? (
        <>
          <SkeletonPanel height="10rem" />
          <Loading label="Loading the validator's report" />
        </>
      ) : (
        <RunDetail run={detail.data} token={token} canRecheck={canRevalidate} />
      )}
      {runs.length > 1 ? (
        <details>
          <summary>All {runs.length} runs on this file</summary>
          <ul className="decisions">
            {runs.map((r) => (
              <li key={r.runId}>
                <Link className="mono" href={`/agent?run=${r.runId}`}>
                  {r.runId}
                </Link>
                <RunStatusBadge status={r.status} />
                <VerdictBadge verdict={r.verdict?.status} />
                <TimeAgo value={r.finishedAt ?? r.createdAt} />
              </li>
            ))}
          </ul>
        </details>
      ) : null}
    </section>
  );
}
