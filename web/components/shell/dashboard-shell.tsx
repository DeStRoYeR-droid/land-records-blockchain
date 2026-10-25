"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState, type ReactNode } from "react";

import { IconMenu } from "@/components/ui/icons";
import { Wordmark } from "@/components/site/wordmark";
import type { MspId } from "@/lib/types";

export interface NavItem {
  href: string;
  label: string;
  /** a small number beside the label (open items) */
  count?: number | null;
  /** match only this exact path (the section's home) */
  exact?: boolean;
  /** a heading shown above this item and the ones after it */
  group?: string;
  external?: boolean;
}

export interface ShellIdentity {
  title: string;
  sub?: string;
  detail?: ReactNode;
  org?: MspId;
  kind?: "citizen" | "officer";
}

/**
 * The frame every dashboard shares: a side column with who you are and where
 * you can go, and the page. Collapses to a top bar below 960px.
 */
export function DashboardShell({
  identity,
  nav,
  onSignOut,
  children,
  label,
  portal = false,
}: {
  identity: ShellIdentity;
  nav: NavItem[];
  onSignOut: () => void;
  children: ReactNode;
  label: string;
  portal?: boolean;
}) {
  const path = usePathname();
  const [open, setOpen] = useState(false);
  useEffect(() => setOpen(false), [path]);
  const active = (n: NavItem) => (n.exact ? path === n.href : path === n.href || path.startsWith(`${n.href}/`));
  return (
    <div className={`shell${portal ? " portal" : ""}`}>
      <aside className="shell-side" data-open={open} aria-label={label}>
        <div className="shell-top">
          <Wordmark href="/" />
          <button
            type="button"
            className="btn btn-small"
            aria-expanded={open}
            aria-controls="shell-menu"
            onClick={() => setOpen((o) => !o)}
          >
            <IconMenu aria-hidden="true" /> {open ? "Close" : "Menu"}
          </button>
        </div>
        <Wordmark href="/" />
        <div className="side-collapsible" id="shell-menu">
          <div className="identity" data-org={identity.org} data-kind={identity.kind}>
            <span className="role">{identity.title}</span>
            {identity.sub ? <span className="sub">{identity.sub}</span> : null}
            {identity.detail}
          </div>
          <nav className="side-nav" aria-label={label}>
            {nav.map((n) => (
              <div key={n.href} style={{ display: "contents" }}>
                {n.group ? <div className="group">{n.group}</div> : null}
                <Link href={n.href} aria-current={active(n) ? "page" : undefined}>
                  <span>{n.label}</span>
                  {n.count ? <span className="count">{n.count}</span> : null}
                </Link>
              </div>
            ))}
          </nav>
          <div className="foot">
            <button type="button" className="btn btn-small" onClick={onSignOut}>
              Sign out
            </button>
            <Link href="/" className="small">
              Back to the public site
            </Link>
          </div>
        </div>
      </aside>
      <main className="shell-main" id="main" tabIndex={-1}>
        {children}
      </main>
    </div>
  );
}
