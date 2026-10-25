import Link from "next/link";

import { Hash } from "@/components/ui/hash";
import { when } from "@/lib/format";
import type { Ownership } from "@/lib/types";

/** The chain of ownership: every owner the ledger has recorded for this parcel, oldest first. */
export function OwnershipChain({ chain, appHref }: { chain: Ownership; appHref?: (appId: string) => string }) {
  const last = chain.links[chain.links.length - 1];
  return (
    <ol className="chain" aria-label={`Chain of ownership: ${chain.owners} owner${chain.owners === 1 ? "" : "s"}`}>
      {chain.links.map((l) => (
        <li key={l.txId} data-current={l === last}>
          <span className="chain-seq" aria-hidden="true">{l.seq}</span>
          <div className="chain-body">
            <span className="chain-owner">
              {l.owner}
              {l === last ? <span className="badge" data-tone="ok" style={{ marginLeft: "0.5rem" }}>Current owner</span> : null}
            </span>
            <span className="small">
              {l.acquired === "registration" ? (
                <>First entered in the digital register</>
              ) : (
                <>
                  Bought through transfer{" "}
                  {l.appId ? (appHref ? <Link href={appHref(l.appId)}>{l.appId}</Link> : <strong>{l.appId}</strong>) : null}
                  {l.decidedBy ? <>, approved by {l.decidedBy}</> : null}
                </>
              )}
            </span>
            <span className="small muted">
              From <time dateTime={l.since}>{when(l.since)}</time> to{" "}
              {l.until ? <time dateTime={l.until}>{when(l.until)}</time> : "today"}
            </span>
            <span className="chain-link-label">
              {l.block !== null ? `block ${l.block} · ` : ""}tx <Hash value={l.txId} head={10} tail={4} />
            </span>
          </div>
        </li>
      ))}
    </ol>
  );
}
