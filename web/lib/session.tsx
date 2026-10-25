"use client";

/**
 * Who is signed in, in this browser. An officer session and a citizen session
 * can exist side by side (handy in a demo: one tab per role).
 *
 * The token is the gateway's HMAC-signed session (8 hours). It is kept in
 * localStorage; the citizen's private key is kept separately (lib/did.ts).
 */

import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";

import type { CitizenClaims, Officer } from "./types";

export interface OfficerSession {
  token: string;
  user: Officer;
}

export interface CitizenSession {
  token: string;
  citizen: CitizenClaims;
}

interface SessionState {
  ready: boolean;
  officer: OfficerSession | null;
  citizen: CitizenSession | null;
  setOfficer: (s: OfficerSession | null) => void;
  setCitizen: (s: CitizenSession | null) => void;
}

const OFFICER_KEY = "landchain.session.officer";
const CITIZEN_KEY = "landchain.session.citizen";

const SessionContext = createContext<SessionState | null>(null);

function tokenExpired(token: string): boolean {
  try {
    const body = token.split(".")[0].replace(/-/g, "+").replace(/_/g, "/");
    const claims = JSON.parse(atob(body + "===".slice((body.length + 3) % 4))) as { exp?: number };
    return !claims.exp || claims.exp * 1000 < Date.now();
  } catch {
    return true;
  }
}

function read<T extends { token: string }>(key: string): T | null {
  try {
    const raw = window.localStorage.getItem(key);
    const value = raw ? (JSON.parse(raw) as T) : null;
    if (value && tokenExpired(value.token)) {
      window.localStorage.removeItem(key);
      return null;
    }
    return value;
  } catch {
    return null;
  }
}

function write(key: string, value: unknown): void {
  try {
    if (value) window.localStorage.setItem(key, JSON.stringify(value));
    else window.localStorage.removeItem(key);
  } catch {
    /* storage blocked: the session lasts for this page only */
  }
}

export function SessionProvider({ children }: { children: ReactNode }) {
  const [ready, setReady] = useState(false);
  const [officer, setOfficerState] = useState<OfficerSession | null>(null);
  const [citizen, setCitizenState] = useState<CitizenSession | null>(null);

  useEffect(() => {
    setOfficerState(read<OfficerSession>(OFFICER_KEY));
    setCitizenState(read<CitizenSession>(CITIZEN_KEY));
    setReady(true);
    const onStorage = (e: StorageEvent) => {
      if (e.key === OFFICER_KEY) setOfficerState(read<OfficerSession>(OFFICER_KEY));
      if (e.key === CITIZEN_KEY) setCitizenState(read<CitizenSession>(CITIZEN_KEY));
    };
    window.addEventListener("storage", onStorage);
    return () => window.removeEventListener("storage", onStorage);
  }, []);

  const setOfficer = useCallback((s: OfficerSession | null) => {
    write(OFFICER_KEY, s);
    setOfficerState(s);
  }, []);
  const setCitizen = useCallback((s: CitizenSession | null) => {
    write(CITIZEN_KEY, s);
    setCitizenState(s);
  }, []);

  // A 401 from the gateway means that session is over (expired, or the gateway's secret changed).
  useEffect(() => {
    const onUnauthorized = (e: Event) => {
      const used = (e as CustomEvent<{ token?: string }>).detail?.token;
      if (!used || officer?.token === used) setOfficer(null);
      if (!used || citizen?.token === used) setCitizen(null);
    };
    window.addEventListener("landchain:unauthorized", onUnauthorized);
    return () => window.removeEventListener("landchain:unauthorized", onUnauthorized);
  }, [officer, citizen, setOfficer, setCitizen]);

  const value = useMemo(
    () => ({ ready, officer, citizen, setOfficer, setCitizen }),
    [ready, officer, citizen, setOfficer, setCitizen],
  );
  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>;
}

export function useSession(): SessionState {
  const ctx = useContext(SessionContext);
  if (!ctx) throw new Error("useSession must be used inside <SessionProvider>");
  return ctx;
}
