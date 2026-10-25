"use client";

import Link from "next/link";
import type { ReactNode } from "react";

import type { ApiError } from "@/lib/api";

/** A blank survey field with a dashed boundary: nothing recorded here yet. */
export function EmptyArt() {
  return (
    <svg className="empty-art" viewBox="0 0 72 56" fill="none" aria-hidden="true">
      <path d="M6 18 40 5l26 20-13 26-41-6z" stroke="currentColor" strokeWidth="2" strokeDasharray="5 4" strokeLinejoin="round" />
      <circle cx="6" cy="18" r="3" fill="currentColor" />
      <circle cx="40" cy="5" r="3" fill="currentColor" />
      <circle cx="66" cy="25" r="3" fill="currentColor" />
      <circle cx="53" cy="51" r="3" fill="currentColor" />
      <circle cx="12" cy="45" r="3" fill="currentColor" />
    </svg>
  );
}

export function EmptyState({
  title,
  children,
  action,
  headingLevel = 2,
}: {
  title: string;
  children?: ReactNode;
  action?: ReactNode;
  headingLevel?: 2 | 3;
}) {
  const H = headingLevel === 2 ? "h2" : "h3";
  return (
    <div className="empty" role="status">
      <EmptyArt />
      <H>{title}</H>
      {children}
      {action ? <div className="cluster">{action}</div> : null}
    </div>
  );
}

/** "Search with no results": say what was searched, and how to widen it. */
export function NoResults({ query, what, onClear, tips }: { query: string; what: string; onClear?: () => void; tips?: string[] }) {
  return (
    <EmptyState
      title={query ? `No ${what} match “${query}”` : `No ${what} here yet`}
      action={
        onClear && query ? (
          <button type="button" className="btn" onClick={onClear}>
            Clear the search
          </button>
        ) : null
      }
    >
      {tips?.length ? (
        <ul>
          {tips.map((t) => (
            <li key={t}>{t}</li>
          ))}
        </ul>
      ) : null}
    </EmptyState>
  );
}

export function ErrorState({
  error,
  title,
  onRetry,
  headingLevel = 2,
}: {
  error: ApiError | Error | string;
  title?: string;
  onRetry?: () => void;
  headingLevel?: 2 | 3;
}) {
  const H = headingLevel === 2 ? "h2" : "h3";
  const message = typeof error === "string" ? error : error.message;
  const status = typeof error === "object" && "status" in error ? (error as ApiError).status : undefined;
  const offline = status === 0 || status === 502 || status === 503 || status === 504;
  const denied = status === 403;
  return (
    <div className="error-state" role="alert">
      <H>{title ?? (offline ? "The network is not answering" : denied ? "Not allowed" : "Something went wrong")}</H>
      <p>{message}</p>
      {offline ? (
        <p className="small">
          On the machine running LandChain, check <code>./landchain.sh status</code> and start what is down with{" "}
          <code>./startup.sh --no-demo</code>.
        </p>
      ) : null}
      {onRetry ? (
        <button type="button" className="btn" onClick={onRetry}>
          Try again
        </button>
      ) : null}
    </div>
  );
}

export function SignInNeeded({ who, href }: { who: string; href: string }) {
  return (
    <EmptyState title={`Sign in as ${who} to see this`} action={<Link className="btn btn-primary" href={href}>Sign in</Link>} />
  );
}
