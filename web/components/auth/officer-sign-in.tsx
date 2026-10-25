"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { api, errorMessage } from "@/lib/api";
import { ORG_NAME, ROLE_TITLE } from "@/lib/format";
import { useSession, type OfficerSession } from "@/lib/session";
import type { MspId, Officer, OfficerRole } from "@/lib/types";

interface Listed {
  username: string;
  org: string;
  title: string;
  duty: string;
  role: OfficerRole;
  mspId: MspId;
}

/** Only same-site paths are followed after sign-in. */
export function safeNext(next: string | undefined, fallback: string): string {
  return next && next.startsWith("/") && !next.startsWith("//") ? next : fallback;
}

export function OfficerSignIn({ next }: { next?: string }) {
  const router = useRouter();
  const { setOfficer, officer } = useSession();
  const [list, setList] = useState<Listed[] | null>(null);
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api<Listed[]>("/api/auth/officers")
      .then(setList)
      .catch(() => setList([]));
  }, []);

  const go = safeNext(next, "/officials");
  useEffect(() => {
    if (officer) router.replace(go);
  }, [officer, go, router]);

  const submit = async () => {
    setBusy(true);
    setError(null);
    try {
      const res = await api<{ token: string; user: Officer }>("/api/auth/officer", { method: "POST", body: { username: username.trim(), password } });
      setOfficer({ token: res.token, user: res.user } satisfies OfficerSession);
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
          <label htmlFor="username">Username</label>
          <input id="username" className="input input-mono" autoComplete="username" value={username} onChange={(e) => setUsername(e.target.value)} required autoCapitalize="none" spellCheck={false} />
        </div>
        <div className="field">
          <label htmlFor="password">Password</label>
          <input id="password" className="input" type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} required />
          <span className="hint">In this demo network each officer&rsquo;s password is the same as the username.</span>
        </div>
        {error ? (
          <p className="error-text" role="alert">
            {error}
          </p>
        ) : null}
        <div className="cluster">
          <button className="btn btn-primary" type="submit" disabled={busy || !username || !password}>
            {busy ? <span className="spinner" aria-hidden="true" /> : null}
            {busy ? "Signing in…" : "Sign in"}
          </button>
        </div>
      </form>
      <section className="panel" aria-labelledby="demo-officers">
        <h2 id="demo-officers" style={{ fontSize: "var(--step-1)", marginBottom: "var(--s-3)" }}>
          Officers on this network
        </h2>
        {list === null ? (
          <p className="faint" role="status">Loading…</p>
        ) : list.length === 0 ? (
          <p className="muted">The officer list could not be loaded. If you run LandChain, check that the gateway is up.</p>
        ) : (
          <ul className="picklist">
            {list.map((o) => (
              <li key={o.username}>
                <button
                  type="button"
                  className="pick"
                  onClick={() => {
                    setUsername(o.username);
                    setPassword(o.username);
                  }}
                >
                  <span className="who">{o.title || ROLE_TITLE[o.role]}</span>
                  <span className="small muted">
                    {ORG_NAME[o.mspId]} · <span className="mono">{o.username}</span>
                  </span>
                </button>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
