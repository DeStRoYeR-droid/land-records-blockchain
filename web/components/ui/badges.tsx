import { ORG_NAME, PARCEL_STATUS, STATUS_INFO, VERDICT_INFO } from "@/lib/format";
import type { AppStatus, MspId, ParcelStatus, Verdict } from "@/lib/types";

export function StatusBadge({ status }: { status: AppStatus }) {
  const info = STATUS_INFO[status] ?? { label: status, tone: "muted" };
  return (
    <span className="badge" data-tone={info.tone}>
      {info.label}
    </span>
  );
}

export function ParcelBadge({ status }: { status: ParcelStatus }) {
  const info = PARCEL_STATUS[status] ?? { label: status, tone: "muted" };
  return (
    <span className="badge" data-tone={info.tone}>
      {info.label}
    </span>
  );
}

/** Dashed outline: the validator's verdict is evidence for a human, not a decision. */
export function VerdictBadge({ verdict, flags }: { verdict: Verdict | null | undefined; flags?: number }) {
  if (!verdict) return <span className="faint small">Not validated</span>;
  const info = VERDICT_INFO[verdict];
  return (
    <span className="badge badge-evidence" data-tone={info.tone} title="The validator's evidence: the decision stays with the officers">
      {info.label}
      {flags ? ` (${flags})` : null}
    </span>
  );
}

const ORG_TONE: Record<MspId, string> = { Org1MSP: "reg", Org2MSP: "rev", Org3MSP: "col" };

export function OrgBadge({ msp }: { msp: MspId }) {
  return (
    <span className="badge" data-tone={ORG_TONE[msp]}>
      {ORG_NAME[msp]}
    </span>
  );
}
