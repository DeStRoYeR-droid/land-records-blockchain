"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import type { ReactNode } from "react";

import { Loading, SkeletonPanel } from "@/components/ui/skeleton";
import { useSession, type CitizenSession, type OfficerSession } from "@/lib/session";

function Gate({ title, children, href, label }: { title: string; children: ReactNode; href: string; label: string }) {
  return (
    <main className="page-narrow" id="main" tabIndex={-1} style={{ paddingBlock: "var(--s-8)" }}>
      <div className="empty" role="status">
        <h1 style={{ fontSize: "var(--step-2)" }}>{title}</h1>
        {children}
        <div className="cluster">
          <Link className="btn btn-primary" href={href}>
            {label}
          </Link>
          <Link className="btn" href="/">
            Back to the public site
          </Link>
        </div>
      </div>
    </main>
  );
}

/** Renders `children(session)` once an officer is signed in; otherwise asks them to sign in. */
export function OfficerGate({ children }: { children: (s: OfficerSession) => ReactNode }) {
  const { ready, officer } = useSession();
  const path = usePathname();
  if (!ready) return <main className="page-narrow" id="main" style={{ paddingBlock: "var(--s-8)" }}><SkeletonPanel height="12rem" /><Loading label="Checking your session" /></main>;
  if (!officer) {
    return (
      <Gate title="Sign in to your desk" href={`/officials/sign-in?next=${encodeURIComponent(path)}`} label="Officer sign-in">
        <p>This page is for department officers. Your actions are signed with your own certificate.</p>
      </Gate>
    );
  }
  return <>{children(officer)}</>;
}

export function CitizenGate({ children }: { children: (s: CitizenSession) => ReactNode }) {
  const { ready, citizen } = useSession();
  const path = usePathname();
  if (!ready) return <main className="page-narrow" id="main" style={{ paddingBlock: "var(--s-8)" }}><SkeletonPanel height="12rem" /><Loading label="Checking your session" /></main>;
  if (!citizen) {
    return (
      <Gate title="Sign in to see your land" href={`/citizen/sign-in?next=${encodeURIComponent(path)}`} label="Citizen sign-in">
        <p>Sign in with your DigiLocker one-time code and the key kept in this browser.</p>
      </Gate>
    );
  }
  return <>{children(citizen)}</>;
}
