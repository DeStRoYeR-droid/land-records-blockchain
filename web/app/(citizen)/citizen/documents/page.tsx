"use client";

import { Hash } from "@/components/ui/hash";
import { IconDownload } from "@/components/ui/icons";
import { EmptyState, ErrorState } from "@/components/ui/states";
import { Loading, SkeletonPanel } from "@/components/ui/skeleton";
import { Timestamp } from "@/components/ui/time";
import { download } from "@/lib/api";
import { docType } from "@/lib/format";
import { usePortal } from "@/lib/portal";
import type { LockerDocument } from "@/lib/types";
import { useApi } from "@/lib/useApi";

export default function Documents() {
  const { token } = usePortal();
  const res = useApi<{ documents: LockerDocument[] }>("/api/citizen/documents", token);
  const docs = res.data?.documents ?? [];
  return (
    <div className="stack-l">
      <header className="dash-head">
        <div>
          <h1>My documents</h1>
          <p>What departments have issued into your DigiLocker. The fingerprint of a document filed with a sale goes on the ledger, so any copy can be checked later.</p>
        </div>
      </header>
      {res.error && !res.data ? (
        <ErrorState error={res.error} onRetry={res.reload} />
      ) : res.loading ? (
        <>
          <SkeletonPanel height="8rem" />
          <Loading label="Loading your documents" />
        </>
      ) : docs.length === 0 ? (
        <EmptyState title="Your DigiLocker is empty">
          <p>The Sub-Registrar issues sale deeds and encumbrance certificates into your DigiLocker.</p>
        </EmptyState>
      ) : (
        <ul className="intents">
          {docs.map((d) => (
            <li key={d.docId} className="panel stack-s stack">
              <div className="panel-head" style={{ marginBottom: 0 }}>
                <h2 style={{ fontSize: "var(--step-1)" }}>{docType(d.docType)}</h2>
                <span className="small muted">
                  from {d.orgName}, <Timestamp value={d.issuedAt} />
                </span>
              </div>
              <p>{d.description}</p>
              <p className="small">
                SHA-256 <Hash value={d.contentSha256} head={14} tail={8} label="document fingerprint" />
              </p>
              {d.content ? (
                <div className="cluster">
                  <button type="button" className="btn btn-small" onClick={() => download(`${d.docType.toLowerCase()}-${d.docId}.txt`, d.content ?? "", "text/plain")}>
                    <IconDownload aria-hidden="true" /> Download
                  </button>
                  <details>
                    <summary>Read it</summary>
                    <pre className="codeblock">{d.content}</pre>
                  </details>
                </div>
              ) : null}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
