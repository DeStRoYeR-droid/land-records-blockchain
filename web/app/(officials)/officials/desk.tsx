"use client";

import { useRouter } from "next/navigation";
import type { ReactNode } from "react";

import { OfficerGate } from "@/components/shell/guards";
import { DashboardShell } from "@/components/shell/dashboard-shell";
import { DeskProvider, useDesk } from "@/lib/desk";
import { ORG_NAME, ROLE_TITLE } from "@/lib/format";
import { deskNav } from "@/lib/officer";
import { useSession } from "@/lib/session";

function Frame({ children }: { children: ReactNode }) {
  const { session, summary } = useDesk();
  const { setOfficer } = useSession();
  const router = useRouter();
  const u = session.user;
  return (
    <DashboardShell
      label="Officers' desk"
      identity={{
        title: u.title || ROLE_TITLE[u.role],
        sub: `${ORG_NAME[u.mspId]} · ${u.username}`,
        detail: <span className="sub">{u.duty}</span>,
        org: u.mspId,
        kind: "officer",
      }}
      nav={deskNav(u.role, summary.data?.queue.length ?? 0)}
      onSignOut={() => {
        setOfficer(null);
        router.push("/");
      }}
    >
      {children}
    </DashboardShell>
  );
}

export function Desk({ children }: { children: ReactNode }) {
  return (
    <OfficerGate>
      {(session) => (
        <DeskProvider session={session}>
          <Frame>{children}</Frame>
        </DeskProvider>
      )}
    </OfficerGate>
  );
}
