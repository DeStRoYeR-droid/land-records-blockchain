"use client";

import { useState } from "react";

import { Dialog } from "@/components/app/dialog";
import { Hash } from "@/components/ui/hash";
import { EmptyState, ErrorState } from "@/components/ui/states";
import { Loading, SkeletonTable } from "@/components/ui/skeleton";
import { TimeAgo } from "@/components/ui/time";
import Link from "next/link";
import { api, errorMessage } from "@/lib/api";
import { useDesk } from "@/lib/desk";
import { rupees } from "@/lib/format";
import type { IntentRecord } from "@/lib/types";
import { useApi } from "@/lib/useApi";

const LABEL: Record<IntentRecord["status"], { text: string; tone: string }> = {
  "awaiting-buyer": { text: "Waiting for the buyer", tone: "warn" },
  ready: { text: "Signed by both", tone: "ok" },
  filed: { text: "Filed", tone: "reg" },
  cancelled: { text: "Cancelled", tone: "muted" },
};

export default function Transfers() {
  const { session, token, summary } = useDesk();
  const res = useApi<{ intents: IntentRecord[] }>("/api/transfer-requests", token, { refreshMs: 8000 });
  const [filing, setFiling] = useState<IntentRecord | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);
  const canFile = session.user.role === "clerk";

  const file = async () => {
    if (!filing) return;
    setBusy(true);
    setError(null);
    try {
      const r = await api<{ appId: string }>("/api/applications", { token, method: "POST", body: { intentId: filing.id } });
      setDone(r.appId);
      setFiling(null);
      res.reload();
      summary.reload();
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  };

  const list = res.data?.intents ?? [];
  return (
    <div className="stack-l">
      <header className="dash-head">
        <div>
          <h1>Transfer requests</h1>
          <p>Sales that citizens have signed with their own keys. A file can be opened only when both seller and buyer have signed.</p>
        </div>
      </header>
      {done ? (
        <div className="callout" data-tone="ok" role="status">
          <strong>File opened.</strong> <Link href={`/officials/applications/${done}`}>Open {done}</Link> to anchor the seller’s documents.
        </div>
      ) : null}
      {res.error && !res.data ? (
        <ErrorState error={res.error} onRetry={res.reload} />
      ) : res.loading ? (
        <>
          <SkeletonTable rows={4} cols={5} />
          <Loading label="Loading transfer requests" />
        </>
      ) : list.length === 0 ? (
        <EmptyState title="No transfer requests yet">
          <p>When a citizen signs a sale in the citizen portal, it appears here.</p>
        </EmptyState>
      ) : (
        <div className="panel panel-flush">
          <div className="table-wrap">
            <table className="table">
              <caption className="visually-hidden">Signed transfer requests</caption>
              <thead>
                <tr>
                  <th scope="col">Parcel</th>
                  <th scope="col">Seller → Buyer</th>
                  <th scope="col" className="right">
                    Consideration
                  </th>
                  <th scope="col">State</th>
                  <th scope="col">Created</th>
                  <th scope="col">
                    <span className="visually-hidden">Action</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {list.map((r) => (
                  <tr key={r.id}>
                    <td className="survey-no">{r.intent.surveyNo}</td>
                    <td>
                      {r.sellerName} → {r.buyerName}
                    </td>
                    <td className="right num">{rupees(r.intent.considerationINR)}</td>
                    <td>
                      <span className="badge" data-tone={LABEL[r.status].tone}>
                        {LABEL[r.status].text}
                      </span>
                      {r.appId ? (
                        <div className="small">
                          <Link className="mono" href={`/officials/applications/${r.appId}`}>
                            {r.appId}
                          </Link>
                        </div>
                      ) : null}
                    </td>
                    <td>
                      <TimeAgo value={r.createdAt} />
                    </td>
                    <td>
                      {r.status === "ready" && canFile ? (
                        <button type="button" className="btn btn-small btn-primary" onClick={() => (setError(null), setFiling(r))}>
                          Open a file
                        </button>
                      ) : null}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      <Dialog open={!!filing} onClose={() => setFiling(null)} title="Open a file from this transfer">
        {filing ? (
          <div className="stack">
            <dl className="kv">
              <dt>Parcel</dt>
              <dd className="survey-no">{filing.intent.surveyNo}</dd>
              <dt>Seller</dt>
              <dd>{filing.sellerName}</dd>
              <dt>Buyer</dt>
              <dd>{filing.buyerName}</dd>
              <dt>Consideration</dt>
              <dd>{rupees(filing.intent.considerationINR)}</dd>
              <dt>Seller’s signature</dt>
              <dd>
                <Hash value={filing.sellerSignature.signature} head={14} tail={6} label="signature" />
              </dd>
              <dt>Buyer’s signature</dt>
              <dd>
                <Hash value={filing.buyerSignature?.signature} head={14} tail={6} label="signature" />
              </dd>
            </dl>
            <p className="small muted">
              The chaincode checks both signatures against the DIDs registered on the ledger. If either does not verify, the file is refused.
            </p>
            {error ? (
              <p className="error-text" role="alert">
                {error}
              </p>
            ) : null}
            <div className="cluster">
              <button type="button" className="btn btn-primary" disabled={busy} onClick={() => void file()}>
                {busy ? <span className="spinner" aria-hidden="true" /> : null}
                {busy ? "Opening…" : "Open the file"}
              </button>
              <button type="button" className="btn" onClick={() => setFiling(null)}>
                Cancel
              </button>
            </div>
          </div>
        ) : null}
      </Dialog>
    </div>
  );
}
