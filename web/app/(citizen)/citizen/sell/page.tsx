"use client";

import Link from "next/link";
import { useState } from "react";

import { Hash } from "@/components/ui/hash";
import { api } from "@/lib/api";
import { signCanonical } from "@/lib/did";
import { area, rupees } from "@/lib/format";
import { usePortal } from "@/lib/portal";
import type { IntentRecord, TransferIntent } from "@/lib/types";
import { useAction } from "@/lib/useApi";

interface Prepared {
  intent: TransferIntent;
  buyerName: string;
  canonical: string;
}

export default function Sell() {
  const { me, token, key } = usePortal();
  const parcels = (me.data?.parcels ?? []).filter((p) => p.status === "ACTIVE");
  const [surveyNo, setSurveyNo] = useState("");
  const [buyer, setBuyer] = useState("");
  const [price, setPrice] = useState("");
  const [prepared, setPrepared] = useState<Prepared | null>(null);
  const [sent, setSent] = useState<IntentRecord | null>(null);

  const prepare = useAction(async () => api<Prepared>("/api/citizen/intents/prepare", { token, method: "POST", body: { surveyNo: surveyNo || parcels[0]?.surveyNo, buyerDigilockerId: buyer.trim(), considerationINR: price } }));
  const send = useAction(async () => {
    if (!prepared || !key) throw new Error("Your signing key is not in this browser. Import your key file first.");
    const signature = signCanonical(key, prepared.intent);
    return api<IntentRecord>("/api/citizen/intents", { token, method: "POST", body: { intent: prepared.intent, signature } });
  });

  if (sent) {
    return (
      <div className="panel stack" role="status">
        <h1 style={{ fontSize: "var(--step-2)" }}>Signed and sent</h1>
        <p>
          Your signed sale of <span className="survey-no">{sent.intent.surveyNo}</span> to <strong>{sent.buyerName}</strong> is waiting for their signature. When they sign, the Registration office can open a file.
        </p>
        <div className="cluster">
          <Link className="btn btn-primary" href="/citizen/transfers">
            See my sales
          </Link>
        </div>
      </div>
    );
  }

  const current = parcels.find((p) => p.surveyNo === (surveyNo || parcels[0]?.surveyNo));
  return (
    <div className="stack-l">
      <header className="dash-head">
        <div>
          <h1>Sell land</h1>
          <p>You and the buyer each sign the same sale with your own keys. Registration cannot open a file without both signatures.</p>
        </div>
      </header>
      {parcels.length === 0 ? (
        <div className="callout" data-tone="warn">
          You have no parcel free to sell. A parcel with a transfer already under way cannot be sold again.
        </div>
      ) : (
        <div className="two-up">
          {!prepared ? (
            <form
              className="panel stack"
              onSubmit={(e) => {
                e.preventDefault();
                void prepare.run().then((r) => r && setPrepared(r));
              }}
            >
              <div className="field">
                <label htmlFor="parcel">Parcel</label>
                <select id="parcel" className="select" value={surveyNo || parcels[0].surveyNo} onChange={(e) => setSurveyNo(e.target.value)}>
                  {parcels.map((p) => (
                    <option key={p.surveyNo} value={p.surveyNo}>
                      {p.surveyNo} — {p.village}, {area(p.areaSqm)}
                    </option>
                  ))}
                </select>
              </div>
              <div className="field">
                <label htmlFor="buyer">Buyer’s DigiLocker ID</label>
                <input id="buyer" className="input input-mono" required value={buyer} onChange={(e) => setBuyer(e.target.value)} spellCheck={false} autoCapitalize="none" />
                <span className="hint">The buyer must have registered on LandChain first.</span>
              </div>
              <div className="field">
                <label htmlFor="price">Sale price (₹)</label>
                <input id="price" className="input" inputMode="numeric" required value={price} onChange={(e) => setPrice(e.target.value.replace(/[^\d,]/g, ""))} />
              </div>
              {prepare.error ? (
                <p className="error-text" role="alert">
                  {prepare.error}
                </p>
              ) : null}
              <div className="cluster">
                <button className="btn btn-primary" type="submit" disabled={prepare.busy || !buyer.trim() || !price}>
                  Review the sale
                </button>
              </div>
            </form>
          ) : (
            <div className="panel stack">
              <h2 style={{ fontSize: "var(--step-1)" }}>Check before you sign</h2>
              <dl className="kv">
                <dt>Parcel</dt>
                <dd className="survey-no">{prepared.intent.surveyNo}</dd>
                <dt>Buyer</dt>
                <dd>{prepared.buyerName}</dd>
                <dt>Price</dt>
                <dd>{rupees(prepared.intent.considerationINR)}</dd>
                <dt>Sale ID</dt>
                <dd>
                  <Hash value={prepared.intent.intentId} head={12} tail={6} label="sale ID" />
                </dd>
              </dl>
              <details>
                <summary>The exact text you are signing</summary>
                <pre className="codeblock">{prepared.canonical}</pre>
              </details>
              {!key ? (
                <p className="callout" data-tone="warn">
                  Your signing key is not in this browser. <Link href="/citizen/key">Import your key file</Link> to sign.
                </p>
              ) : null}
              {send.error ? (
                <p className="error-text" role="alert">
                  {send.error}
                </p>
              ) : null}
              <div className="cluster">
                <button type="button" className="btn btn-primary" disabled={send.busy || !key} onClick={() => void send.run().then((r) => r && setSent(r))}>
                  {send.busy ? <span className="spinner" aria-hidden="true" /> : null}
                  Sign with my key
                </button>
                <button type="button" className="btn" onClick={() => setPrepared(null)}>
                  Change something
                </button>
              </div>
            </div>
          )}
          <aside className="panel stack-s stack" aria-label="About this step">
            <h2 style={{ fontSize: "var(--step-1)" }}>{current ? current.village : "About this step"}</h2>
            <p className="muted">
              Signing does not transfer anything yet. The sale becomes a file only after the buyer signs and Registration opens it, then Revenue and the Collector must each do their part.
            </p>
          </aside>
        </div>
      )}
    </div>
  );
}
