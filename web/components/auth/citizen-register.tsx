"use client";

import Link from "next/link";
import { useState } from "react";

import { Hash } from "@/components/ui/hash";
import { IconKey } from "@/components/ui/icons";
import { api, download, errorMessage } from "@/lib/api";
import { generateKey, keyFile, rememberAccount, saveKey, signCanonical, type CitizenKey } from "@/lib/did";
import { useSession } from "@/lib/session";
import type { CitizenClaims } from "@/lib/types";

type Done = { key: CitizenKey; claims: CitizenClaims };

export function CitizenRegister() {
  const { setCitizen } = useSession();
  const [name, setName] = useState("");
  const [mobile, setMobile] = useState("");
  const [last4, setLast4] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<Done | null>(null);
  const [saved, setSaved] = useState(false);

  const submit = async () => {
    setBusy(true);
    setError(null);
    try {
      // The key is made here, in the browser; only the public half (inside the DID) is ever sent.
      const key = generateKey();
      const { challenge } = await api<{ challenge: string }>("/api/citizen/challenge", { method: "POST", body: { purpose: "register", did: key.did } });
      const signature = signCanonical(key, { type: "landchain.register/v1", did: key.did, challenge });
      const res = await api<{ token: string; citizen: CitizenClaims }>("/api/citizen/register", {
        method: "POST",
        body: { name: name.trim(), mobile, aadhaarLast4: last4, did: key.did, challenge, signature },
      });
      saveKey(key);
      rememberAccount({ digilockerId: res.citizen.digilockerId, did: key.did, name: res.citizen.name });
      setCitizen({ token: res.token, citizen: res.citizen });
      setDone({ key, claims: res.citizen });
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  };

  if (done) {
    return (
      <div className="panel stack" role="status">
        <h2 style={{ fontSize: "var(--step-2)" }}>You are registered, {done.claims.name.split(" ")[0]}.</h2>
        <dl className="kv">
          <dt>DigiLocker ID</dt>
          <dd>
            <Hash value={done.claims.digilockerId} head={40} label="DigiLocker ID" />
          </dd>
          <dt>Your DID</dt>
          <dd>
            <Hash value={done.key.did} head={18} tail={8} label="DID" />
          </dd>
        </dl>
        <div className="callout" data-tone="warn">
          <strong>Save your key file now.</strong> It is the only copy of your private key. This browser keeps one, but if you clear its data or use another device, the file is how you sign in again. Anyone who has it can sign sales as you, so keep it offline.
        </div>
        <div className="cluster">
          <button
            type="button"
            className="btn btn-primary"
            onClick={() => {
              download(`landchain-key-${done.claims.digilockerId}.json`, keyFile(done.key, { digilockerId: done.claims.digilockerId, name: done.claims.name, did: done.key.did }), "application/json");
              setSaved(true);
            }}
          >
            <IconKey aria-hidden="true" /> Download my key file
          </button>
          <Link className="btn" href="/citizen" aria-disabled={!saved} style={saved ? undefined : { opacity: 0.6 }}>
            Go to my land
          </Link>
        </div>
        {!saved ? <p className="small muted">You can continue without downloading, but you will not be able to recover the key.</p> : null}
      </div>
    );
  }

  return (
    <form
      className="panel stack"
      onSubmit={(e) => {
        e.preventDefault();
        void submit();
      }}
    >
      <div className="field">
        <label htmlFor="name">Full name</label>
        <input id="name" className="input" autoComplete="name" required minLength={3} value={name} onChange={(e) => setName(e.target.value)} />
        <span className="hint">As on your Aadhaar. The register shows only your initials to the public.</span>
      </div>
      <div className="form-row">
        <div className="field">
          <label htmlFor="mobile">Mobile number</label>
          <input id="mobile" className="input" inputMode="numeric" autoComplete="tel-national" pattern="[0-9]{10}" maxLength={10} required value={mobile} onChange={(e) => setMobile(e.target.value.replace(/\D/g, ""))} />
        </div>
        <div className="field">
          <label htmlFor="last4">Last 4 digits of Aadhaar</label>
          <input id="last4" className="input" inputMode="numeric" pattern="[0-9]{4}" maxLength={4} required value={last4} onChange={(e) => setLast4(e.target.value.replace(/\D/g, ""))} />
        </div>
      </div>
      <p className="small muted">
        This creates a DigiLocker account and a signing key. The key is generated in this browser; only its public half (your DID) goes to the ledger, so no one else, including the departments, can sign for you.
      </p>
      {error ? (
        <p className="error-text" role="alert">
          {error}
        </p>
      ) : null}
      <div className="cluster">
        <button className="btn btn-primary" type="submit" disabled={busy || name.trim().length < 3 || mobile.length !== 10 || last4.length !== 4}>
          {busy ? <span className="spinner" aria-hidden="true" /> : null}
          {busy ? "Creating your key…" : "Register"}
        </button>
        <Link className="btn btn-quiet" href="/citizen/sign-in">
          I already have an account
        </Link>
      </div>
    </form>
  );
}
