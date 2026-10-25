"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

import { RunDetail, RunStatusBadge } from "@/components/agent/run-detail";
import { Score } from "@/components/app/parts";
import { VerdictBadge } from "@/components/ui/badges";
import { EmptyState, ErrorState, NoResults } from "@/components/ui/states";
import { Loading, SkeletonPanel, SkeletonTable } from "@/components/ui/skeleton";
import { TimeAgo } from "@/components/ui/time";
import { duration } from "@/lib/format";
import type { AgentRun } from "@/lib/types";
import { useApi } from "@/lib/useApi";

type Row = Omit<AgentRun, "steps" | "checks"> & { stepCount?: number };

export function RunsTab({ token, canRecheck, selected }: { token: string; canRecheck: boolean; selected: string | null }) {
  const router = useRouter();
  const [q, setQ] = useState("");
  const list = useApi<{ runs: Row[] }>("/api/agent/runs?limit=100", token, { refreshMs: 6000 });
  const detail = useApi<AgentRun>(selected ? `/api/agent/runs/${selected}` : null, token, { refreshMs: 4000 });
  const rows = (list.data?.runs ?? []).filter((r) => {
    const t = q.trim().toLowerCase();
    return !t || [r.runId, r.appId, r.surveyNo, r.status, r.verdict?.status].some((x) => String(x ?? "").toLowerCase().includes(t));
  });
  const open = (id: string | null) => router.replace(id ? `/agent?run=${id}` : "/agent?tab=runs", { scroll: false });

  if (selected) {
    return (
      <div className="stack-l">
        <button type="button" className="btn btn-small" style={{ justifySelf: "start" }} onClick={() => open(null)}>
          ← All runs
        </button>
        {detail.error && !detail.data ? (
          <ErrorState error={detail.error} onRetry={detail.reload} title={detail.error.status === 404 ? "No such run" : undefined} />
        ) : !detail.data ? (
          <>
            <SkeletonPanel height="12rem" />
            <Loading label="Loading the run" />
          </>
        ) : (
          <RunDetail run={detail.data} token={token} canRecheck={canRecheck} />
        )}
      </div>
    );
  }

  return (
    <div className="stack-l">
      <form className="searchbar" role="search" aria-label="Search runs" onSubmit={(e) => e.preventDefault()}>
        <label htmlFor="rq" className="visually-hidden">
          Search runs
        </label>
        <input id="rq" className="input" type="search" placeholder="Application, run, status or verdict" value={q} onChange={(e) => setQ(e.target.value)} autoComplete="off" />
      </form>
      {list.error && !list.data ? (
        <ErrorState error={list.error} onRetry={list.reload} />
      ) : list.loading ? (
        <>
          <SkeletonTable rows={6} cols={6} />
          <Loading label="Loading runs" />
        </>
      ) : (list.data?.runs.length ?? 0) === 0 ? (
        <EmptyState title="The validator has not run yet">
          <p>It starts when a file reaches Revenue. File a transfer, or press “Validate again” on a file.</p>
        </EmptyState>
      ) : rows.length === 0 ? (
        <NoResults what="runs" query={q} onClear={() => setQ("")} tips={["Search an application id like APP-20261013-AB12.", "Or a status such as anchored, quarantined or failed."]} />
      ) : (
        <div className="panel panel-flush">
          <div className="table-wrap">
            <table className="table">
              <caption className="visually-hidden">Validator runs</caption>
              <thead>
                <tr>
                  <th scope="col">Run</th>
                  <th scope="col">Application</th>
                  <th scope="col">Status</th>
                  <th scope="col">Verdict</th>
                  <th scope="col">Faithfulness</th>
                  <th scope="col">Took</th>
                  <th scope="col">When</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => (
                  <tr key={r.runId}>
                    <td>
                      <button type="button" className="row-link mono linklike" onClick={() => open(r.runId)}>
                        {r.runId}
                      </button>
                    </td>
                    <td>
                      <span className="mono">{r.appId}</span>
                      <div className="small muted survey-no">{r.surveyNo}</div>
                    </td>
                    <td>
                      <RunStatusBadge status={r.status} /> {r.resumed ? <span className="badge" data-tone="warn">resumed</span> : null}
                    </td>
                    <td>
                      <VerdictBadge verdict={r.verdict?.status} flags={r.verdict?.flagCount} />
                    </td>
                    <td>
                      <Score value={r.quality?.aggregate.minFaithfulness} label="Lowest faithfulness" />
                      {r.quality?.aggregate.fallbacks ? <span className="small muted"> template ×{r.quality.aggregate.fallbacks}</span> : null}
                    </td>
                    <td className="num">{duration(r.durationMs)}</td>
                    <td>
                      <TimeAgo value={r.finishedAt ?? r.createdAt} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}
