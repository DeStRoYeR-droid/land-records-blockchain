"use client";

import { createContext, useContext, type ReactNode } from "react";

import type { ApiState } from "./useApi";
import { useApi } from "./useApi";
import type { OfficerSession } from "./session";
import type { OfficerSummary } from "./types";

interface Desk {
  session: OfficerSession;
  summary: ApiState<OfficerSummary>;
  /** the token every desk request uses */
  token: string;
}

const DeskContext = createContext<Desk | null>(null);

export function DeskProvider({ session, children }: { session: OfficerSession; children: ReactNode }) {
  const summary = useApi<OfficerSummary>("/api/officer/summary", session.token, { refreshMs: 8000 });
  return <DeskContext.Provider value={{ session, summary, token: session.token }}>{children}</DeskContext.Provider>;
}

export function useDesk(): Desk {
  const ctx = useContext(DeskContext);
  if (!ctx) throw new Error("useDesk must be used inside the officers' desk");
  return ctx;
}
