"use client";

import { useState, type FormEvent } from "react";

import { RegisterParcel } from "@/components/desk/register-parcel";
import { api } from "@/lib/api";
import { useDesk } from "@/lib/desk";
import { docType } from "@/lib/format";
import { useAction } from "@/lib/useApi";

type Lookup = { digilockerId: string; name: string; registered: boolean };

function Field({ id, label, hint, ...rest }: { id: string; label: string; hint?: string } & React.InputHTMLAttributes<HTMLInputElement>) {
  return (
    <div className="field">
      <label htmlFor={id}>{label}</label>
      <input id={id} className="input" {...rest} />
      {hint ? <span className="hint">{hint}</span> : null}
    </div>
  );
}

function LookupBox({ onFound }: { onFound?: (l: Lookup) => void }) {
  const { token } = useDesk();
  const [id, setId] = useState("");
  const look = useAction((v: string) => api<Lookup>(`/api/citizens/lookup?digilockerId=${encodeURIComponent(v)}`, { token }));
  return (
    <div className="stack-s stack">
      <div className="field">
        <label htmlFor="dl-id">DigiLocker ID</label>
        <div className="searchbar">
          <input id="dl-id" className="input input-mono" value={id} onChange={(e) => setId(e.target.value)} placeholder="DL-…" spellCheck={false} autoCapitalize="none" />
          <button type="button" className="btn" disabled={look.busy || !id.trim()} onClick={() => look.run(id.trim()).then((r) => r && onFound?.(r))}>
            Look up
          </button>
        </div>
      </div>
      {look.error ? (
        <p className="error-text" role="alert">
          {look.error}
        </p>
      ) : null}
      {look.result ? (
        <p className="callout" data-tone={look.result.registered ? "ok" : "warn"} role="status">
          <strong>{look.result.name}</strong> — {look.result.registered ? "has a LandChain identity" : "has not registered a LandChain identity yet"}
        </p>
      ) : null}
    </div>
  );
}

export default function Registry() {
  const { token, session } = useDesk();
  const isRegistrar = session.user.role === "registrar";
  const [msg, setMsg] = useState<{ tone: "ok" | "bad"; text: string } | null>(null);

  const post = useAction(async (path: string, body: unknown) => api<{ txId?: string; docId?: string }>(path, { token, method: "POST", body }));

  const submit = (path: string, ok: string) => async (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    const form = e.currentTarget;
    const data = Object.fromEntries(new FormData(form).entries());
    setMsg(null);
    const r = await post.run(path, data);
    if (r) {
      setMsg({ tone: "ok", text: ok });
      form.reset();
    }
  };

  if (!isRegistrar) {
    return (
      <div className="callout" data-tone="warn">
        Only the Sub-Registrar registers parcels and issues documents.
      </div>
    );
  }
  return (
    <div className="stack-l">
      <header className="dash-head">
        <div>
          <h1>Register &amp; issue</h1>
          <p>Record a new parcel and its boundary on the ledger, or issue a sale deed or encumbrance certificate into a citizen’s DigiLocker.</p>
        </div>
      </header>
      {msg ? (
        <div className="callout" data-tone={msg.tone} role="status">
          {msg.text}
        </div>
      ) : null}
      {post.error ? (
        <div className="callout" data-tone="bad" role="alert">
          {post.error}
        </div>
      ) : null}
      <div className="grid-2">
        <RegisterParcel />

        <div className="stack">
          <form className="panel stack" onSubmit={submit("/api/documents/issue", "Document issued to DigiLocker.")}>
            <h2 style={{ fontSize: "var(--step-1)" }}>Issue a document</h2>
            <div className="field">
              <label htmlFor="docType">Document</label>
              <select id="docType" name="docType" className="select" defaultValue="SALEDEED">
                <option value="SALEDEED">{docType("SALEDEED")}</option>
                <option value="ENCUMBRANCE">{docType("ENCUMBRANCE")}</option>
              </select>
            </div>
            <Field id="digilockerId" name="digilockerId" label="Holder’s DigiLocker ID" required className="input input-mono" spellCheck={false} />
            <Field id="i-surveyNo" name="surveyNo" label="Survey number" required className="input input-mono" />
            <div className="form-row">
              <Field id="i-village" name="village" label="Village" />
              <Field id="i-district" name="district" label="District" />
            </div>
            <div className="form-row">
              <Field id="extentSqm" name="extentSqm" label="Extent in the deed (sq.m)" inputMode="numeric" />
              <Field id="considerationINR" name="considerationINR" label="Consideration (₹)" inputMode="numeric" />
            </div>
            <div className="form-row">
              <Field id="vendor" name="vendor" label="Vendor (seller)" />
              <Field id="purchaser" name="purchaser" label="Purchaser (buyer)" />
            </div>
            <p className="hint small muted">A sale deed needs all of the fields above; an encumbrance certificate needs only the survey number.</p>
            <div className="cluster">
              <button className="btn btn-primary" type="submit" disabled={post.busy}>
                Issue the document
              </button>
            </div>
          </form>
          <section className="panel stack" aria-labelledby="h-lookup">
            <h2 id="h-lookup" style={{ fontSize: "var(--step-1)" }}>
              Look up a citizen
            </h2>
            <LookupBox />
          </section>
        </div>
      </div>
    </div>
  );
}
