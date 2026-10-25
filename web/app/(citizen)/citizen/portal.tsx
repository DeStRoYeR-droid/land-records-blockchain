"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import type { ReactNode } from "react";

import { CitizenGate } from "@/components/shell/guards";
import { DashboardShell, type NavItem } from "@/components/shell/dashboard-shell";
import { PortalProvider, usePortal } from "@/lib/portal";
import { useSession } from "@/lib/session";

function Frame({ children }: { children: ReactNode }) {
  const { session, me, key, keyChecked } = usePortal();
  const { setCitizen } = useSession();
  const router = useRouter();
  const incoming = (me.data?.intents ?? []).filter((i) => i.status === "awaiting-buyer" && i.buyerDigilockerId === session.citizen.digilockerId).length;
  const nav: NavItem[] = [
    { href: "/citizen", label: "My land", exact: true },
    { href: "/citizen/transfers", label: "Sales and purchases", count: incoming || null },
    { href: "/citizen/sell", label: "Sell land" },
    { href: "/citizen/documents", label: "My documents", group: "DigiLocker" },
    { href: "/citizen/access-log", label: "Who read them" },
    { href: "/citizen/key", label: "My signing key", group: "Security" },
    { href: "/verify", label: "Verify a report" },
  ];
  return (
    <DashboardShell
      portal
      label="Citizen portal"
      identity={{ title: session.citizen.name, sub: session.citizen.digilockerId, kind: "citizen" }}
      nav={nav}
      onSignOut={() => {
        setCitizen(null);
        router.push("/");
      }}
    >
      {keyChecked && !key ? (
        <div className="callout" data-tone="warn" style={{ marginBottom: "var(--s-5)" }}>
          <strong>Your signing key is not in this browser.</strong> You can read your land, but to sell or accept a purchase you need to <Link href="/citizen/key">import your key file</Link>.
        </div>
      ) : null}
      {children}
    </DashboardShell>
  );
}

export function Portal({ children }: { children: ReactNode }) {
  return (
    <CitizenGate>
      {(session) => (
        <PortalProvider session={session}>
          <Frame>{children}</Frame>
        </PortalProvider>
      )}
    </CitizenGate>
  );
}
