"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { api, errorMessage } from "@/lib/api";
import { knownAccounts, loadKey, parseKeyFile, rememberAccount, saveKey, signCanonical, type KnownAccount } from "@/lib/did";
import { useSession } from "@/lib/session";
import type { CitizenClaims } from "@/lib/types";

import { safeNext } from "./officer-sign-in";

export function CitizenSignIn({ next }: { next?: string }) {
  const router = useRouter();
  const { setCitizen, citizen } = useSession();
  const [accounts, setAccounts] = useState<KnownAccount[]>([]);
  const [dl, setDl] = useState("");
  const [otp, setOtp] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [keyDid, setKeyDid] = useState<string | null>(null);

  const go = safeNext(next, "/citizen");
  useEffect(() => {
    const a = knownAccounts();
    setAccounts(a);
    if (a[0]) setDl(a[0].digilockerId);
  }, []);
  useEffect(() => {
    if (citizen) router.replace(go);
  }, [citizen, go, router]);

  const account = accounts.find((a) => a.digilockerId === dl.trim());
  const haveKey = !!(account && loadKey(account.did)) || (!!keyDid && !!loadKey(keyDid));

  const importFile = async (f: File | null) => {
    if (!f) return;
    setError(null);
    try {
      const { key, account: acc } = parseKeyFile(await f.text());
      saveKey(key);
      const id = acc.digilockerId ?? dl.trim();
      if (id) {
        rememberAccount({ digilockerId: id, did: key.did, name: acc.name ?? "" });
        setAccounts(knownAccounts());
        setDl(id);
      }
      setKeyDid(key.did);
    } catch (e) {
      setError(errorMessage(e));
    }
  };

  const submit = async () => {
    setBusy(true);
    setError(null);
    try {
      const id = dl.trim();
      const did = account?.did ?? keyDid;
      const key = did ? loadKey(did) : null;
      if (!key) throw new Error("This browser does not have the key for that account. Import your key file below.");
      const { challenge } = await api<{ challenge: string }>("/api/citizen/challenge", { method: "POST", body: { purpose: "login", digilockerId: id } });
      const signature = signCanonical(key, { type: "landchain.login/v1", digilockerId: id, challenge });
      const res = await api<{ token: string; citizen: CitizenClaims }>("/api/citizen/login", { method: "POST", body: { digilockerId: id, otp, challenge, signature } });
      setCitizen({ token: res.token, citizen: res.citizen });
      router.replace(go);
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="two-up">
      <form
        className="panel stack"
        onSubmit={(e) => {
          e.preventDefault();
          void submit();
        }}
      >
        <div className="field">
          <label htmlFor="dl">DigiLocker ID</label>
          <input id="dl" className="input input-mono" list="known" value={dl} onChange={(e) => setDl(e.target.value)} required spellCheck={false} autoCapitalize="none" autoComplete="username" />
          <datalist id="known">
            {accounts.map((a) => (
              <option key={a.digilockerId} value={a.digilockerId}>
                {a.name}
              </option>
            ))}
          </datalist>
          <span className="hint">{account ? `${account.name || "Account"} — this browser holds your key.` : "Shown to you when you registered."}</span>
        </div>
        <div className="field">
          <label htmlFor="otp">One-time code from DigiLocker</label>
          <input id="otp" className="input otp" inputMode="numeric" autoComplete="one-time-code" maxLength={6} required value={otp} onChange={(e) => setOtp(e.target.value.replace(/\D/g, ""))} />
          <span className="hint">On this demo network the code is 123456.</span>
        </div>
        <p className="small muted">Signing in also signs a one-time challenge with your private key. That proves it is you, not just someone with the code.</p>
        {error ? (
          <p className="error-text" role="alert">
            {error}
          </p>
        ) : null}
        <div className="cluster">
          <button className="btn btn-primary" type="submit" disabled={busy || !dl.trim() || otp.length !== 6}>
            {busy ? <span className="spinner" aria-hidden="true" /> : null}
            {busy ? "Signing in…" : "Sign in"}
          </button>
          <Link className="btn btn-quiet" href="/citizen/register">
            Register instead
          </Link>
        </div>
      </form>
      <section className="panel stack" aria-labelledby="h-key">
        <h2 id="h-key" style={{ fontSize: "var(--step-1)" }}>
          New browser or device?
        </h2>
        <p className="muted">Your private key lives in the browser where you registered. Bring it here with the key file you downloaded.</p>
        <div className="field">
          <label htmlFor="keyfile">Key file</label>
          <input id="keyfile" className="input" type="file" accept=".json,application/json" onChange={(e) => void importFile(e.target.files?.[0] ?? null)} />
        </div>
        {haveKey ? <p className="callout" data-tone="ok" role="status">The key is ready in this browser.</p> : null}
      </section>
    </div>
  );
}
