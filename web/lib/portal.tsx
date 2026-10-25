"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";

import { loadKey, parseKeyFile, saveKey, rememberAccount, type CitizenKey } from "./did";
import type { CitizenSession } from "./session";
import type { CitizenMe } from "./types";
import { useApi, type ApiState } from "./useApi";

interface Portal {
  session: CitizenSession;
  token: string;
  me: ApiState<CitizenMe>;
  /** the private key, if this browser has it; null means the citizen must import their key file */
  key: CitizenKey | null;
  keyChecked: boolean;
  importKey: (text: string) => void;
}

const PortalContext = createContext<Portal | null>(null);

export function PortalProvider({ session, children }: { session: CitizenSession; children: ReactNode }) {
  const me = useApi<CitizenMe>("/api/citizen/me", session.token, { refreshMs: 10000 });
  const [key, setKey] = useState<CitizenKey | null>(null);
  const [keyChecked, setChecked] = useState(false);
  useEffect(() => {
    setKey(loadKey(session.citizen.did));
    setChecked(true);
  }, [session.citizen.did]);

  const importKey = useCallback(
    (text: string) => {
      const { key: k, account } = parseKeyFile(text);
      if (k.did !== session.citizen.did) throw new Error("This key belongs to a different account than the one signed in.");
      saveKey(k);
      rememberAccount({ digilockerId: session.citizen.digilockerId, did: k.did, name: account.name ?? session.citizen.name });
      setKey(k);
    },
    [session],
  );

  const value = useMemo(() => ({ session, token: session.token, me, key, keyChecked, importKey }), [session, me, key, keyChecked, importKey]);
  return <PortalContext.Provider value={value}>{children}</PortalContext.Provider>;
}

export function usePortal(): Portal {
  const ctx = useContext(PortalContext);
  if (!ctx) throw new Error("usePortal must be used inside the citizen portal");
  return ctx;
}
