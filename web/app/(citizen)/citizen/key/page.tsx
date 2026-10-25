"use client";

import { useState } from "react";

import { Hash } from "@/components/ui/hash";
import { IconKey } from "@/components/ui/icons";
import { download, errorMessage } from "@/lib/api";
import { forgetKey, keyFile } from "@/lib/did";
import { usePortal } from "@/lib/portal";

export default function KeyPage() {
  const { session, key, keyChecked, importKey, me } = usePortal();
  const [problem, setProblem] = useState<string | null>(null);
  const [ok, setOk] = useState(false);
  const [confirm, setConfirm] = useState(false);
  const onChain = me.data?.profile.onChain?.did;

  const upload = async (f: File | null) => {
    if (!f) return;
    setProblem(null);
    setOk(false);
    try {
      importKey(await f.text());
      setOk(true);
    } catch (e) {
      setProblem(errorMessage(e));
    }
  };

  return (
    <div className="stack-l">
      <header className="dash-head">
        <div>
          <h1>My signing key</h1>
          <p>Every sale you sign, and every sign-in, is signed with this key. The ledger holds only its public half.</p>
        </div>
      </header>
      <section className="panel stack" aria-labelledby="h-did">
        <h2 id="h-did" style={{ fontSize: "var(--step-1)" }}>
          Your identity (DID)
        </h2>
        <dl className="kv">
          <dt>DID</dt>
          <dd>
            <Hash value={session.citizen.did} full />
          </dd>
          <dt>On the ledger</dt>
          <dd>{onChain ? (onChain === session.citizen.did ? "Registered; it matches this account." : "A different DID is registered.") : "Checking…"}</dd>
          <dt>In this browser</dt>
          <dd>{!keyChecked ? "Checking…" : key ? "The private key is here." : "The private key is not here."}</dd>
        </dl>
        {key ? (
          <div className="cluster">
            <button
              type="button"
              className="btn btn-primary"
              onClick={() => download(`landchain-key-${session.citizen.digilockerId}.json`, keyFile(key, { digilockerId: session.citizen.digilockerId, name: session.citizen.name, did: key.did }), "application/json")}
            >
              <IconKey aria-hidden="true" /> Download my key file
            </button>
            {confirm ? (
              <>
                <button
                  type="button"
                  className="btn btn-danger"
                  onClick={() => {
                    forgetKey(key.did);
                    location.reload();
                  }}
                >
                  Yes, remove it from this browser
                </button>
                <button type="button" className="btn" onClick={() => setConfirm(false)}>
                  Keep it
                </button>
              </>
            ) : (
              <button type="button" className="btn" onClick={() => setConfirm(true)}>
                Remove from this browser
              </button>
            )}
          </div>
        ) : null}
        {confirm ? <p className="callout" data-tone="warn">Without the key file you cannot sign again. Make sure you have downloaded it.</p> : null}
      </section>
      <section className="panel stack" aria-labelledby="h-import">
        <h2 id="h-import" style={{ fontSize: "var(--step-1)" }}>
          Use my key on this device
        </h2>
        <div className="field">
          <label htmlFor="kf">Key file</label>
          <input id="kf" className="input" type="file" accept=".json,application/json" onChange={(e) => void upload(e.target.files?.[0] ?? null)} />
          <span className="hint">The file you downloaded when you registered.</span>
        </div>
        {ok ? <p className="callout" data-tone="ok" role="status">Key imported. You can sign again.</p> : null}
        {problem ? (
          <p className="error-text" role="alert">
            {problem}
          </p>
        ) : null}
      </section>
    </div>
  );
}
