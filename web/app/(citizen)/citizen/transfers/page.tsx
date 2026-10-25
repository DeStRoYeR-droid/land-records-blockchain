"use client";

import { useState } from "react";

import { Dialog } from "@/components/app/dialog";
import { EmptyState, ErrorState } from "@/components/ui/states";
import { Loading, SkeletonTable } from "@/components/ui/skeleton";
import { TimeAgo } from "@/components/ui/time";
import Link from "next/link";
import { api } from "@/lib/api";
import { signCanonical } from "@/lib/did";
import { rupees } from "@/lib/format";
import { usePortal } from "@/lib/portal";
import type { IntentRecord } from "@/lib/types";
import { useAction } from "@/lib/useApi";

const STATE: Record<IntentRecord["status"], { text: string; tone: string }> = {
  "awaiting-buyer": { text: "Waiting for the buyer", tone: "warn" },
  ready: { text: "Signed by both: waiting for Registration", tone: "ok" },
  filed: { text: "Filed with Registration", tone: "reg" },
  cancelled: { text: "Cancelled", tone: "muted" },
};

export default function Transfers() {
  const { session, me, token, key } = usePortal();
  const [review, setReview] = useState<IntentRecord | null>(null);
  const accept = useAction(async (r: IntentRecord) => {
    if (!key) throw new Error("Your signing key is not in this browser. Import your key file first.");
    return api(`/api/citizen/intents/${r.id}/accept`, { token, method: "POST", body: { signature: signCanonical(key, r.intent) } });
  });
  const cancel = useAction(async (r: IntentRecord) => api(`/api/citizen/intents/${r.id}/cancel`, { token, method: "POST" }));
  const list = [...(me.data?.intents ?? [])].sort((a, b) => b.createdAt.localeCompare(a.createdAt));
  const mine = session.citizen.digilockerId;

  return (
    <div className="stack-l">
      <header className="dash-head">
        <div>
          <h1>Sales and purchases</h1>
          <p>Each sale carries two signatures: the seller’s and the buyer’s. You can cancel one until Registration files it.</p>
        </div>
      </header>
      {me.error && !me.data ? (
        <ErrorState error={me.error} onRetry={me.reload} />
      ) : !me.data ? (
        <>
          <SkeletonTable rows={3} cols={4} />
          <Loading label="Loading your sales" />
        </>
      ) : list.length === 0 ? (
        <EmptyState title="No sales yet" action={<Link className="btn btn-primary" href="/citizen/sell">Sell land</Link>}>
          <p>Sales you start, and purchases others offer you, are listed here.</p>
        </EmptyState>
      ) : (
        <ul className="intents">
          {list.map((r) => {
            const buying = r.buyerDigilockerId === mine;
            return (
              <li key={r.id} className="panel stack-s stack">
                <div className="panel-head" style={{ marginBottom: 0 }}>
                  <h2 style={{ fontSize: "var(--step-1)" }}>
                    {buying ? "Buying" : "Selling"} <span className="survey-no">{r.intent.surveyNo}</span>
                  </h2>
                  <span className="badge" data-tone={STATE[r.status].tone}>
                    {STATE[r.status].text}
                  </span>
                </div>
                <p>
                  {buying ? `From ${r.sellerName}` : `To ${r.buyerName}`} for {rupees(r.intent.considerationINR)} · started <TimeAgo value={r.createdAt} />
                </p>
                {r.appId ? (
                  <p>
                    <Link href={`/citizen/files/${r.appId}`}>Follow file <span className="mono">{r.appId}</span></Link>
                  </p>
                ) : null}
                <div className="cluster">
                  {buying && r.status === "awaiting-buyer" ? (
                    <button type="button" className="btn btn-primary" onClick={() => (accept.reset(), setReview(r))}>
                      Review and sign
                    </button>
                  ) : null}
                  {r.status === "awaiting-buyer" || r.status === "ready" ? (
                    <button type="button" className="btn" disabled={cancel.busy} onClick={() => void cancel.run(r).then(() => me.reload())}>
                      Cancel
                    </button>
                  ) : null}
                </div>
              </li>
            );
          })}
        </ul>
      )}
      {cancel.error ? (
        <p className="error-text" role="alert">
          {cancel.error}
        </p>
      ) : null}

      <Dialog open={!!review} onClose={() => setReview(null)} title="Sign this purchase">
        {review ? (
          <div className="stack">
            <dl className="kv">
              <dt>Parcel</dt>
              <dd className="survey-no">{review.intent.surveyNo}</dd>
              <dt>Seller</dt>
              <dd>{review.sellerName}</dd>
              <dt>Price</dt>
              <dd>{rupees(review.intent.considerationINR)}</dd>
            </dl>
            <p className="small muted">Your signature says you agree to buy at this price. The chaincode will check it against your DID before any file is opened.</p>
            {accept.error ? (
              <p className="error-text" role="alert">
                {accept.error}
              </p>
            ) : null}
            <div className="cluster">
              <button
                type="button"
                className="btn btn-primary"
                disabled={accept.busy || !key}
                onClick={() => void accept.run(review).then((r) => r && (setReview(null), me.reload()))}
              >
                {accept.busy ? <span className="spinner" aria-hidden="true" /> : null}
                Sign with my key
              </button>
              <button type="button" className="btn" onClick={() => setReview(null)}>
                Not now
              </button>
            </div>
          </div>
        ) : null}
      </Dialog>
    </div>
  );
}
