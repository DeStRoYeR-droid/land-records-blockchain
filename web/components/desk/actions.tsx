"use client";

import { useState, type ReactNode } from "react";

import { Mark } from "@/components/ui/icons";
import { Hash } from "@/components/ui/hash";
import { api } from "@/lib/api";
import { area, docType, VERDICT_INFO } from "@/lib/format";
import type { Application, OfficerRole, Parcel, SellerDocument } from "@/lib/types";
import { useAction, useApi } from "@/lib/useApi";

interface Props {
  app: Application;
  parcel: Parcel;
  role: OfficerRole;
  token: string;
  onDone: () => void;
}

function Box({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="panel stack" aria-label={title}>
      <h2 style={{ fontSize: "var(--step-1)" }}>{title}</h2>
      {children}
    </section>
  );
}

function Problem({ text }: { text: string | null }) {
  return text ? (
    <p className="error-text" role="alert">
      {text}
    </p>
  ) : null;
}

function Busy({ busy, children }: { busy: boolean; children: ReactNode }) {
  return (
    <>
      {busy ? <span className="spinner" aria-hidden="true" /> : null}
      {children}
    </>
  );
}

function OtpField({ value, onChange, id }: { value: string; onChange: (v: string) => void; id: string }) {
  return (
    <div className="field">
      <label htmlFor={id}>Seller’s DigiLocker one-time code</label>
      <input id={id} className="input otp" inputMode="numeric" autoComplete="one-time-code" maxLength={6} value={value} onChange={(e) => onChange(e.target.value.replace(/\D/g, ""))} />
      <span className="hint">DigiLocker sends the seller a code each time a department asks to read a document. On this demo network it is always 123456.</span>
    </div>
  );
}

// ---- clerk: anchor the seller's documents -----------------------------------------------

function AnchorDocuments({ app, token, onDone }: Props) {
  const docs = useApi<{ documents: SellerDocument[] }>(`/api/applications/${app.appId}/seller-documents`, token);
  const [otp, setOtp] = useState("");
  const [which, setWhich] = useState<string | null>(null);
  const anchor = useAction(async (docId: string) => api(`/api/applications/${app.appId}/anchor`, { token, method: "POST", body: { docId, otp } }));
  return (
    <Box title="Anchor the seller’s documents">
      <p className="muted">
        Each document is read from the seller’s DigiLocker with their consent, fingerprinted (SHA-256) and written to the ledger. Revenue will fetch its own copy later and compare.
      </p>
      <OtpField id="anchor-otp" value={otp} onChange={setOtp} />
      {docs.loading ? (
        <p className="faint" role="status">Loading the seller’s documents…</p>
      ) : docs.error ? (
        <Problem text={docs.error.message} />
      ) : docs.data?.documents.length ? (
        <ul className="doclist">
          {docs.data.documents.map((d) => (
            <li key={d.docId}>
              <div>
                <strong>{docType(d.docType)}</strong> <span className="small muted">from {d.orgName}</span>
                <div className="small muted">{d.description}</div>
                <Hash value={d.contentSha256} head={10} tail={6} label="document fingerprint" />
              </div>
              {d.anchored ? (
                <span className="badge" data-tone="ok">
                  <Mark state="ok" /> Anchored
                </span>
              ) : (
                <button
                  type="button"
                  className="btn btn-small btn-primary"
                  disabled={anchor.busy || otp.length !== 6}
                  onClick={() => {
                    setWhich(d.docId);
                    void anchor.run(d.docId).then((r) => {
                      if (r) {
                        docs.reload();
                        onDone();
                      }
                    });
                  }}
                >
                  <Busy busy={anchor.busy && which === d.docId}>Anchor</Busy>
                </button>
              )}
            </li>
          ))}
        </ul>
      ) : (
        <p className="muted">The seller has no documents in DigiLocker yet. The Sub-Registrar issues the deed and the certificate.</p>
      )}
      <Problem text={anchor.error} />
    </Box>
  );
}

// ---- registrar --------------------------------------------------------------------------------

function ForwardToRevenue({ app, token, onDone }: Props) {
  const fwd = useAction(async () => api(`/api/applications/${app.appId}/forward`, { token, method: "POST" }));
  const ready = app.documents.length > 0;
  return (
    <Box title="Forward to Revenue">
      <p className="muted">
        {ready
          ? `${app.documents.length} document${app.documents.length === 1 ? " is" : "s are"} anchored. Forwarding hands the file to Revenue, and the validator begins its first review.`
          : "Documents must be anchored by the clerk before this file can be forwarded."}
      </p>
      <div className="cluster">
        <button type="button" className="btn btn-primary" disabled={!ready || fwd.busy} onClick={() => void fwd.run().then((r) => r && onDone())}>
          <Busy busy={fwd.busy}>Forward to Revenue</Busy>
        </button>
      </div>
      <Problem text={fwd.error} />
    </Box>
  );
}

// ---- verifier --------------------------------------------------------------------------------------

function VerifyDocuments({ app, token, onDone }: Props) {
  const [otp, setOtp] = useState("");
  const verify = useAction(async () =>
    api<{ checked: { docId: string; anchored: string; computed: string; match: boolean }[] }>(`/api/applications/${app.appId}/verify`, { token, method: "POST", body: { otp } }),
  );
  return (
    <Box title="Verify the documents">
      <p className="muted">
        Revenue fetches its own copy of each document with the seller’s consent, hashes it itself, and the chaincode compares those hashes with the ones Registration anchored. Any difference flags the file.
      </p>
      <OtpField id="verify-otp" value={otp} onChange={setOtp} />
      <div className="cluster">
        <button type="button" className="btn btn-primary" disabled={verify.busy || otp.length !== 6} onClick={() => void verify.run().then((r) => r && onDone())}>
          <Busy busy={verify.busy}>Fetch and compare</Busy>
        </button>
      </div>
      <Problem text={verify.error} />
      {verify.result ? (
        <ol className="checklist" aria-label="Comparison result">
          {verify.result.checked.map((c) => (
            <li key={c.docId}>
              <Mark state={c.match ? "ok" : "fail"} />
              <div>
                <div className="title">{c.docId}</div>
                <div className="detail">{c.match ? "Revenue’s copy matches the anchored fingerprint." : "The fingerprints differ: the document changed after it was anchored."}</div>
              </div>
            </li>
          ))}
        </ol>
      ) : null}
    </Box>
  );
}

function ForwardToCollectorate({ app, token, onDone }: Props) {
  const fwd = useAction(async () => api(`/api/applications/${app.appId}/forward-collectorate`, { token, method: "POST" }));
  const s = app.survey;
  return (
    <Box title="Forward to the Collectorate">
      <p className="muted">
        {s ? `The survey measured ${area(s.measuredAreaSqm)} against ${area(s.recordedAreaSqm)} recorded (${s.deviationPct.toFixed(1)}% apart, ${s.withinTolerance ? "within" : "outside"} the ${s.tolerancePct}% tolerance).` : "The survey has not been recorded."} The validator reviews the file again when it reaches the Collector.
      </p>
      <div className="cluster">
        <button type="button" className="btn btn-primary" disabled={fwd.busy} onClick={() => void fwd.run().then((r) => r && onDone())}>
          <Busy busy={fwd.busy}>Forward to the Collectorate</Busy>
        </button>
      </div>
      <Problem text={fwd.error} />
    </Box>
  );
}

// ---- surveyor -------------------------------------------------------------------------------------------

function RecordSurvey({ app, parcel, token, onDone }: Props) {
  const [measured, setMeasured] = useState("");
  const [boundaries, setBoundaries] = useState("");
  const survey = useAction(async () => api(`/api/applications/${app.appId}/survey`, { token, method: "POST", body: { measuredAreaSqm: measured, boundaries } }));
  return (
    <Box title="Record the field survey">
      <p className="muted">
        The register holds {area(parcel.areaSqm)} for this parcel. The ledger works out the deviation and whether it is within tolerance.
      </p>
      <form
        className="stack"
        onSubmit={(e) => {
          e.preventDefault();
          void survey.run().then((r) => r && onDone());
        }}
      >
        <div className="field">
          <label htmlFor="measured">Measured area (sq.m)</label>
          <input id="measured" className="input" inputMode="decimal" required value={measured} onChange={(e) => setMeasured(e.target.value)} />
        </div>
        <div className="field">
          <label htmlFor="boundaries">Boundaries</label>
          <textarea id="boundaries" className="textarea" required value={boundaries} onChange={(e) => setBoundaries(e.target.value)} placeholder="North: … South: … East: … West: …" />
          <span className="hint">Compass bearings and landmarks, as in the field book.</span>
        </div>
        <div className="cluster">
          <button className="btn btn-primary" type="submit" disabled={survey.busy || !measured || !boundaries.trim()}>
            <Busy busy={survey.busy}>Record the survey</Busy>
          </button>
        </div>
        <Problem text={survey.error} />
      </form>
    </Box>
  );
}

// ---- collector ------------------------------------------------------------------------------------------------

function Decide({ app, token, onDone }: Props) {
  const [remarks, setRemarks] = useState("");
  const [confirm, setConfirm] = useState<"approve" | "reject" | null>(null);
  const decide = useAction(async (kind: "approve" | "reject") =>
    api(`/api/applications/${app.appId}/${kind}`, { token, method: "POST", body: kind === "approve" ? { remarks } : { reason: remarks } }),
  );
  const v = app.validations.at(-1);
  return (
    <Box title="Your decision">
      {v ? (
        <p className="callout" data-tone={v.verdict === "clear" ? "ok" : v.verdict === "attention" ? "warn" : "bad"}>
          <strong>Validator:</strong> {VERDICT_INFO[v.verdict].label}. {VERDICT_INFO[v.verdict].plain} This is evidence; the decision is yours.
        </p>
      ) : (
        <p className="callout" data-tone="warn">
          The validator has not reported on this file yet.
        </p>
      )}
      <div className="field">
        <label htmlFor="remarks">Remarks (kept on the ledger)</label>
        <textarea id="remarks" className="textarea" value={remarks} onChange={(e) => setRemarks(e.target.value)} placeholder="Your reasons, for the record" />
      </div>
      {confirm ? (
        <div className="callout" data-tone={confirm === "approve" ? "col" : "bad"} role="alertdialog" aria-label="Confirm your decision">
          <p>
            <strong>{confirm === "approve" ? "Approve this transfer?" : "Reject this application?"}</strong>{" "}
            {confirm === "approve" ? "The parcel’s owner changes on the ledger and cannot be edited afterwards." : "The file is closed and the parcel is released."}
          </p>
          <div className="cluster" style={{ marginTop: "var(--s-3)" }}>
            <button type="button" className={confirm === "approve" ? "btn btn-approve" : "btn btn-danger"} disabled={decide.busy} onClick={() => void decide.run(confirm).then((r) => (setConfirm(null), r && onDone()))}>
              <Busy busy={decide.busy}>{confirm === "approve" ? "Yes, approve" : "Yes, reject"}</Busy>
            </button>
            <button type="button" className="btn" onClick={() => setConfirm(null)}>
              Not yet
            </button>
          </div>
        </div>
      ) : (
        <div className="cluster">
          <button type="button" className="btn btn-approve" onClick={() => setConfirm("approve")}>
            Approve
          </button>
          <button type="button" className="btn btn-danger" onClick={() => setConfirm("reject")} disabled={!remarks.trim()} title={remarks.trim() ? undefined : "Write the reason first"}>
            Reject
          </button>
        </div>
      )}
      <Problem text={decide.error} />
    </Box>
  );
}

// ---- which action is yours --------------------------------------------------------------------------------------

export function Actions(props: Props) {
  const { app, role } = props;
  if (role === "clerk" && app.status === "SUBMITTED") return <AnchorDocuments {...props} />;
  if (role === "registrar" && app.status === "SUBMITTED") return <ForwardToRevenue {...props} />;
  if (role === "verifier" && app.status === "WITH_REVENUE") return <VerifyDocuments {...props} />;
  if (role === "surveyor" && app.status === "DOCS_VERIFIED") return <RecordSurvey {...props} />;
  if (role === "verifier" && app.status === "SURVEYED") return <ForwardToCollectorate {...props} />;
  if (role === "collector" && (app.status === "WITH_COLLECTORATE" || app.status === "FLAGGED")) return <Decide {...props} />;
  return (
    <section className="panel" aria-label="Your action">
      <h2 style={{ fontSize: "var(--step-1)", marginBottom: "var(--s-2)" }}>Nothing for you to do here</h2>
      <p className="muted">
        {app.status === "APPROVED" || app.status === "REJECTED"
          ? "This file is closed."
          : "This file is not at your desk right now. You can still read everything on it."}
      </p>
    </section>
  );
}
