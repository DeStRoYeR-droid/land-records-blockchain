import Link from "next/link";

import { StatusBadge, VerdictBadge } from "@/components/ui/badges";
import { gwUrl } from "@/lib/api";
import { day } from "@/lib/format";
import type { TransferSummary } from "@/lib/types";

/** Every transfer file on a parcel, each with its PGP-signed public record. */
export function TransferRecords({ transfers, appHref }: { transfers: TransferSummary[]; appHref?: (appId: string) => string }) {
  if (!transfers.length) {
    return <p className="muted">No transfer has been filed on this parcel yet.</p>;
  }
  return (
    <div className="records">
      {[...transfers].reverse().map((t) => (
        <div className="record-card" key={t.appId}>
          <div className="stack-s">
            <h3>
              {appHref ? <Link href={appHref(t.appId)}>{t.appId}</Link> : t.appId} <StatusBadge status={t.status} />
              {t.verdicts.length ? <> <VerdictBadge verdict={t.verdicts[t.verdicts.length - 1]} /></> : null}
            </h3>
            <p className="small">
              {t.seller} to {t.buyer} · opened {day(t.openedAt)}
              {t.decidedAt ? ` · ${t.decision === "APPROVED" ? "approved" : "rejected"} ${day(t.decidedAt)}` : ""}
            </p>
          </div>
          <a className="btn btn-small" href={gwUrl(t.publicRecord)} download>
            Download the public record (PGP-signed)
          </a>
        </div>
      ))}
      <p className="small muted">
        Each record is a text file with its OpenPGP signature inside, signed by the Records Office key the Collector
        attested on the ledger. Check one with <code>gpg --verify</code> or on the <Link href="/verify">verify page</Link>.
        The seller and the buyer can download a fuller copy of their own from their dashboard.
      </p>
    </div>
  );
}
