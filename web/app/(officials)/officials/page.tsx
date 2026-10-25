"use client";

import Link from "next/link";

import { StatusBadge, VerdictBadge } from "@/components/ui/badges";
import { EmptyState, ErrorState } from "@/components/ui/states";
import { Loading, SkeletonPanel, SkeletonTable } from "@/components/ui/skeleton";
import { TimeAgo } from "@/components/ui/time";
import { useDesk } from "@/lib/desk";
import { LIFECYCLE, ROLE_TITLE, STATUS_INFO, parcelPath, plural } from "@/lib/format";
import { DESK_BLURB } from "@/lib/officer";

const SEG: Record<string, string> = { reg: "var(--reg)", rev: "var(--rev)", col: "var(--col)", ok: "var(--field)", bad: "var(--red)" };

export default function DeskHome() {
  const { session, summary } = useDesk();
  const u = session.user;
  const s = summary.data;
  return (
    <div className="stack-l">
      <header className="dash-head">
        <div>
          <h1>{ROLE_TITLE[u.role]}</h1>
          <p>{DESK_BLURB[u.role]}</p>
        </div>
        {u.role === "clerk" && s?.readyIntents ? (
          <Link className="btn btn-primary" href="/officials/transfers">
            {plural(s.readyIntents, "signed transfer")} ready to file
          </Link>
        ) : null}
      </header>

      {summary.error && !s ? (
        <ErrorState error={summary.error} onRetry={summary.reload} />
      ) : !s ? (
        <>
          <SkeletonPanel height="4rem" />
          <SkeletonTable rows={4} cols={5} />
          <Loading label="Loading your queue" />
        </>
      ) : (
        <>
          <section aria-labelledby="h-where">
            <h2 id="h-where" className="visually-hidden">
              Where the files are
            </h2>
            <div className="pipeline">
              {[...LIFECYCLE, "FLAGGED", "REJECTED"].map((st) => {
                const info = STATUS_INFO[st as keyof typeof STATUS_INFO];
                return (
                  <Link key={st} href={`/officials/applications?status=${st}`} style={{ ["--seg" as string]: SEG[info.tone] }}>
                    <span className="n">{s.byStatus[st as keyof typeof s.byStatus] ?? 0}</span>
                    <span className="l">{info.label}</span>
                  </Link>
                );
              })}
            </div>
          </section>

          <section aria-labelledby="h-queue">
            <div className="panel panel-flush">
              <div className="panel-head">
                <h2 id="h-queue">Waiting for you</h2>
                <span className="muted small">{plural(s.queue.length, "file")}</span>
              </div>
              {s.queue.length === 0 ? (
                <div style={{ padding: "var(--s-5)" }}>
                  <EmptyState title="Nothing is waiting for you" headingLevel={3}>
                    <p>Files appear here when they reach your desk. This list refreshes by itself.</p>
                  </EmptyState>
                </div>
              ) : (
                <div className="table-wrap">
                  <table className="table">
                    <caption className="visually-hidden">Files waiting for your action</caption>
                    <thead>
                      <tr>
                        <th scope="col">File</th>
                        <th scope="col">Your action</th>
                        <th scope="col">Seller → Buyer</th>
                        <th scope="col">Validator</th>
                        <th scope="col">Since</th>
                      </tr>
                    </thead>
                    <tbody>
                      {s.queue.map((q) => (
                        <tr key={q.appId}>
                          <td>
                            <Link className="row-link" href={`/officials/applications/${q.appId}`}>
                              <span className="mono">{q.appId}</span>
                            </Link>
                            <div className="small muted">
                              <span className="survey-no">{q.surveyNo}</span>
                            </div>
                          </td>
                          <td>
                            <StatusBadge status={q.status} /> <strong>{q.action}</strong>
                          </td>
                          <td>
                            {q.seller} → {q.buyer}
                          </td>
                          <td>
                            <VerdictBadge verdict={q.validation} />
                          </td>
                          <td>
                            <TimeAgo value={q.updatedAt} />
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          </section>

          <section aria-labelledby="h-recent">
            <div className="panel">
              <div className="panel-head">
                <h2 id="h-recent">Recent movement</h2>
                <span className="muted small">across all departments</span>
              </div>
              {s.recent.length === 0 ? (
                <p className="muted">No files have been filed yet.</p>
              ) : (
                <ul className="decisions">
                  {s.recent.map((r) => (
                    <li key={r.appId}>
                      <Link href={`/officials/applications/${r.appId}`} className="mono">
                        {r.appId}
                      </Link>
                      <Link href={parcelPath(r.surveyNo, "/officials/parcels")} className="survey-no">
                        {r.surveyNo}
                      </Link>
                      <StatusBadge status={r.status} />
                      <span className="small muted">{r.last ? `${r.last.action} — ${r.last.by}` : ""}</span>
                      <TimeAgo value={r.updatedAt} />
                    </li>
                  ))}
                </ul>
              )}
            </div>
          </section>
        </>
      )}
    </div>
  );
}
