"use client";

import Link from "next/link";
import { useEffect } from "react";

/** The generic application error state, shared by every error boundary. */
export function ErrorPanel({
  error,
  reset,
  home = "/",
  homeLabel = "Go to the home page",
}: {
  error: Error & { digest?: string };
  reset: () => void;
  home?: string;
  homeLabel?: string;
}) {
  useEffect(() => {
    console.error(error);
  }, [error]);
  return (
    <div className="page-narrow" style={{ paddingBlock: "var(--s-6)" }}>
      <div className="error-state" role="alert">
        <p className="faint small">Application error</p>
        <h1 style={{ fontSize: "var(--step-3)" }}>This page stopped working</h1>
        <p>
          Something went wrong while showing this page. Nothing was half-done: an action either reached the ledger in
          full or not at all, so it is safe to try again.
        </p>
        {error.digest ? (
          <p className="small faint">
            Reference <code>{error.digest}</code> (search for it in logs/web.log).
          </p>
        ) : null}
        <div className="cluster">
          <button type="button" className="btn btn-primary" onClick={() => reset()}>
            Try again
          </button>
          <Link className="btn" href={home}>
            {homeLabel}
          </Link>
        </div>
      </div>
    </div>
  );
}
