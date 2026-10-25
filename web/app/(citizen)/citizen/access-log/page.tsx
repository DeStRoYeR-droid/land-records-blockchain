"use client";

import { EmptyState, ErrorState } from "@/components/ui/states";
import { Loading, SkeletonTable } from "@/components/ui/skeleton";
import { Timestamp } from "@/components/ui/time";
import { usePortal } from "@/lib/portal";
import type { AccessLogEntry } from "@/lib/types";
import { useApi } from "@/lib/useApi";

export default function AccessLog() {
  const { token } = usePortal();
  const res = useApi<{ entries: AccessLogEntry[] }>("/api/citizen/access-log", token, { refreshMs: 15000 });
  const list = res.data?.entries ?? [];
  return (
    <div className="stack-l">
      <header className="dash-head">
        <div>
          <h1>Who read my documents</h1>
          <p>A department can read a document in your DigiLocker only with your one-time code. Every attempt is recorded here.</p>
        </div>
      </header>
      {res.error && !res.data ? (
        <ErrorState error={res.error} onRetry={res.reload} />
      ) : res.loading ? (
        <>
          <SkeletonTable rows={5} cols={4} />
          <Loading label="Loading the access log" />
        </>
      ) : list.length === 0 ? (
        <EmptyState title="No one has read your documents">
          <p>When a department asks to read a document, the request appears here.</p>
        </EmptyState>
      ) : (
        <div className="panel panel-flush">
          <div className="table-wrap">
            <table className="table">
              <caption className="visually-hidden">Access log</caption>
              <thead>
                <tr>
                  <th scope="col">When</th>
                  <th scope="col">Department</th>
                  <th scope="col">What happened</th>
                  <th scope="col">Purpose</th>
                </tr>
              </thead>
              <tbody>
                {list.map((e, i) => (
                  <tr key={i}>
                    <td className="nowrap">
                      <Timestamp value={e.at} />
                    </td>
                    <td>{e.department}</td>
                    <td>{e.action}</td>
                    <td>{e.purpose ?? "—"}</td>
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
