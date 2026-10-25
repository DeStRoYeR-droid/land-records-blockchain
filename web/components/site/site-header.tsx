"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import { useSession } from "@/lib/session";

import { Wordmark } from "./wordmark";

const NAV = [
  { href: "/register", label: "Land register" },
  { href: "/verify", label: "Verify a report" },
  { href: "/how-it-works", label: "How it works" },
];

export function SiteHeader() {
  const path = usePathname();
  const { ready, officer, citizen } = useSession();
  const signedIn = ready && (officer || citizen);
  return (
    <header className="site-header">
      <div className="page">
        <Wordmark />
        <nav className="site-nav" aria-label="Main">
          {NAV.map((n) => (
            <Link key={n.href} href={n.href} aria-current={path === n.href || path.startsWith(`${n.href}/`) ? "page" : undefined}>
              {n.label}
            </Link>
          ))}
          {signedIn ? (
            <Link className="btn btn-primary btn-small" href={officer ? "/officials" : "/citizen"}>
              {officer ? "Your desk" : "Your land"}
            </Link>
          ) : (
            <Link className="btn btn-primary btn-small" href="/sign-in">
              Sign in
            </Link>
          )}
        </nav>
      </div>
    </header>
  );
}
