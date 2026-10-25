"use client";

import { useState } from "react";

import { IconDownload } from "@/components/ui/icons";
import { errorMessage, type Token } from "@/lib/api";
import { downloadRecord, publicRecordPath, verifyRecord, type RecordCheck } from "@/lib/records";

/**
 * A transfer's two signed records: the public copy (anyone) and, for the seller or
 * the buyer, their own copy. Both carry their OpenPGP signature inside the file;
 * "Check the signature" verifies the downloaded file here, against the key the
 * ledger attests.
 */
export function RecordDownloads({ appId, token, role }: { appId: string; token?: Token; role?: "seller" | "buyer" | null }) {
  const [last, setLast] = useState<{ text: string; what: string } | null>(null);
  const [check, setCheck] = useState<RecordCheck | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [problem, setProblem] = useState<string | null>(null);

  const get = async (what: "party" | "public") => {
    setProblem(null);
    setCheck(null);
    setBusy(what);
    try {
      const text = what === "party"
        ? await downloadRecord(`/api/citizen/records/${encodeURIComponent(appId)}`, `landchain-${appId}-${role}-copy.asc`, token)
        : await downloadRecord(publicRecordPath(appId), `landchain-${appId}-public.asc`);
      setLast({ text, what: what === "party" ? `your ${role}'s copy` : "the public copy" });
    } catch (e) {
      setProblem(errorMessage(e));
    } finally {
      setBusy(null);
    }
  };

  const verify = async () => {
    if (!last) return;
    setBusy("verify");
    try {
      setCheck(await verifyRecord(last.text));
    } catch (e) {
      setProblem(errorMessage(e));
    } finally {
      setBusy(null);
    }
  };

  return (
    <section className="panel stack" aria-labelledby={`rec-${appId}`}>
      <div className="panel-head" style={{ marginBottom: 0 }}>
        <h2 id={`rec-${appId}`} style={{ fontSize: "var(--step-1)" }}>Signed transfer record</h2>
        <span className="small muted">OpenPGP, Records Office key</span>
      </div>
      <p className="small muted">
        {role
          ? "Your copy has both parties’ names, the consideration, both consent signatures, every officer’s step, the survey and who read your documents. The public copy shows people as initials and leaves out amounts; anyone can download it."
          : "The public copy shows the parcel, its boundary, every step with its ledger transaction, the document and report fingerprints and the chain of ownership, with people as initials."}
      </p>
      <div className="cluster">
        {role ? (
          <button type="button" className="btn btn-primary" onClick={() => void get("party")} disabled={busy !== null}>
            <IconDownload /> {busy === "party" ? "Preparing…" : `Download my copy (${role})`}
          </button>
        ) : null}
        <button type="button" className="btn" onClick={() => void get("public")} disabled={busy !== null}>
          <IconDownload /> {busy === "public" ? "Preparing…" : "Download the public copy"}
        </button>
        {last ? (
          <button type="button" className="btn btn-quiet" onClick={() => void verify()} disabled={busy !== null}>
            {busy === "verify" ? "Checking…" : `Check the signature of ${last.what}`}
          </button>
        ) : null}
      </div>
      {problem ? <p className="error-text" role="alert">{problem}</p> : null}
      {check ? (
        <div className="callout" data-tone={check.valid ? "ok" : "bad"} role="status">
          <p>
            <strong>{check.valid ? "Signature valid." : "This record does not verify."}</strong>{" "}
            {check.recordId ? `${check.recordId} (${check.copy} copy${check.issuedTo ? `, issued to ${check.issuedTo}` : ""}).` : ""}
          </p>
          <ul className="small">
            {check.steps.map((s) => (
              <li key={s.label}>{s.ok === null ? "–" : s.ok ? "✓" : "✗"} {s.label}{s.detail ? `: ${s.detail}` : ""}</li>
            ))}
          </ul>
        </div>
      ) : null}
    </section>
  );
}
