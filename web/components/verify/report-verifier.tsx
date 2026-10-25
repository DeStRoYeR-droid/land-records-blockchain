"use client";

/**
 * Checks a validator report in this browser and shows each step as it runs.
 * Used on the public /verify page, on a citizen's own file and in the
 * validator console.
 */

import { useCallback, useState } from "react";

import { Fingerprint, Hash } from "@/components/ui/hash";
import { Mark } from "@/components/ui/icons";
import { Stamp } from "@/components/ui/stamp";
import { download } from "@/lib/api";
import { VERDICT_INFO, when } from "@/lib/format";
import type { Verdict } from "@/lib/types";
import { verifyReport, type VerifyOutcome, type VerifyStep } from "@/lib/verify";

export function useReportVerifier() {
  const [steps, setSteps] = useState<VerifyStep[] | null>(null);
  const [outcome, setOutcome] = useState<VerifyOutcome | null>(null);
  const [busy, setBusy] = useState(false);
  const run = useCallback(async (bytes: Uint8Array, signature: string) => {
    setBusy(true);
    setOutcome(null);
    try {
      const out = await verifyReport(bytes, signature, setSteps);
      setOutcome(out);
      return out;
    } finally {
      setBusy(false);
    }
  }, []);
  const reset = useCallback(() => {
    setSteps(null);
    setOutcome(null);
  }, []);
  return { steps, outcome, busy, run, reset };
}

export function VerifySteps({ steps }: { steps: VerifyStep[] }) {
  return (
    <ol className="checklist" aria-label="Verification steps">
      {steps.map((s) => (
        <li key={s.id}>
          <Mark state={s.state} />
          <div>
            <div className="title">{s.label}</div>
            {s.detail ? <div className="detail">{s.detail}</div> : null}
            {s.state === "skip" && !s.detail ? <div className="detail">Not reached</div> : null}
          </div>
        </li>
      ))}
    </ol>
  );
}

export function VerifyOutcomeView({ outcome }: { outcome: VerifyOutcome }) {
  const r = outcome.report;
  const verdict = r?.verdict?.status as Verdict | undefined;
  return (
    <div className="verify-outcome" aria-live="polite">
      <div className="verify-stamp">
        {outcome.valid ? (
          <Stamp tone="ok" sub="Signed by the validator, anchored on the ledger">
            VERIFIED
          </Stamp>
        ) : (
          <Stamp tone="bad" sub={outcome.reason ?? "See the steps below"}>
            NOT VERIFIED
          </Stamp>
        )}
      </div>
      <dl className="kv">
        {r?.reportId ? (
          <>
            <dt>Report</dt>
            <dd className="mono">{r.reportId}</dd>
          </>
        ) : null}
        {r?.applicationId ? (
          <>
            <dt>Application</dt>
            <dd className="mono">{r.applicationId}</dd>
          </>
        ) : null}
        {verdict && VERDICT_INFO[verdict] ? (
          <>
            <dt>What it found</dt>
            <dd>
              {VERDICT_INFO[verdict].label}: {VERDICT_INFO[verdict].plain}
            </dd>
          </>
        ) : null}
        {r?.generatedAt ? (
          <>
            <dt>Written</dt>
            <dd>{when(r.generatedAt)}</dd>
          </>
        ) : null}
        <dt>SHA-256</dt>
        <dd>
          <Hash value={outcome.sha256} full />
        </dd>
        {outcome.signerFingerprint ? (
          <>
            <dt>Signing key</dt>
            <dd>
              <Fingerprint value={outcome.signerFingerprint} />
            </dd>
          </>
        ) : null}
        {outcome.anchor && outcome.anchor.anchored ? (
          <>
            <dt>Anchored</dt>
            <dd>
              {when(outcome.anchor.anchoredAt)}, transaction <Hash value={outcome.anchor.txId} />
            </dd>
          </>
        ) : null}
      </dl>
    </div>
  );
}

/** How to repeat the check with nothing but GnuPG and the files. */
export function OfflineCheck({ publicKey, reportName = "report.json" }: { publicKey?: string | null; reportName?: string }) {
  return (
    <details className="offline-check">
      <summary>Check it without this website (GnuPG)</summary>
      <div className="stack-s stack" style={{ marginTop: "var(--s-3)" }}>
        <p className="small muted">
          Save the validator&rsquo;s public key, then let gpg check the signature. Compare the fingerprint gpg prints with
          the one attested on the ledger.
        </p>
        {publicKey ? (
          <button type="button" className="btn btn-small" onClick={() => download("landchain-validator.asc", publicKey, "text/plain")}>
            Save the validator&rsquo;s public key
          </button>
        ) : null}
        <pre className="codeblock">{`gpg --import landchain-validator.asc
gpg --verify ${reportName}.asc ${reportName}
sha256sum ${reportName}      # compare with the anchored SHA-256`}</pre>
      </div>
    </details>
  );
}
