"use client";

import { useState } from "react";

import { Hash } from "@/components/ui/hash";
import { Mark } from "@/components/ui/icons";
import { Stamp } from "@/components/ui/stamp";
import { docType, when } from "@/lib/format";
import { verifyRecord, type RecordCheck } from "@/lib/records";
import { verifyDocument } from "@/lib/verify";

import { FilePick } from "./file-pick";
import { OfflineCheck, useReportVerifier, VerifyOutcomeView, VerifySteps } from "./report-verifier";

type Tab = "report" | "record" | "document";

async function readBytes(f: File): Promise<Uint8Array> {
  return new Uint8Array(await f.arrayBuffer());
}

export function VerifyForm() {
  const [tab, setTab] = useState<Tab>("report");
  return (
    <div className="stack">
      <div className="tabs" role="tablist" aria-label="What to verify">
        <button type="button" role="tab" id="tab-report" className="tab" aria-selected={tab === "report"} aria-controls="panel-report" onClick={() => setTab("report")}>
          A validator report
        </button>
        <button type="button" role="tab" id="tab-record" className="tab" aria-selected={tab === "record"} aria-controls="panel-record" onClick={() => setTab("record")}>
          A transfer record
        </button>
        <button type="button" role="tab" id="tab-document" className="tab" aria-selected={tab === "document"} aria-controls="panel-document" onClick={() => setTab("document")}>
          A document
        </button>
      </div>
      <div role="tabpanel" id="panel-report" aria-labelledby="tab-report" hidden={tab !== "report"}>
        <ReportForm />
      </div>
      <div role="tabpanel" id="panel-record" aria-labelledby="tab-record" hidden={tab !== "record"}>
        <RecordForm />
      </div>
      <div role="tabpanel" id="panel-document" aria-labelledby="tab-document" hidden={tab !== "document"}>
        <DocumentForm />
      </div>
    </div>
  );
}

function ReportForm() {
  const [report, setReport] = useState<File | null>(null);
  const [sigFile, setSigFile] = useState<File | null>(null);
  const [pasted, setPasted] = useState("");
  const [problem, setProblem] = useState<string | null>(null);
  const [publicKey, setPublicKey] = useState<string | null>(null);
  const v = useReportVerifier();

  const submit = async () => {
    setProblem(null);
    if (!report) return setProblem("Choose the report file (report.json).");
    const signature = sigFile ? await sigFile.text() : pasted;
    if (!signature.includes("BEGIN PGP SIGNATURE")) {
      return setProblem("Choose the signature file (report.json.asc) or paste the signature, starting with -----BEGIN PGP SIGNATURE-----.");
    }
    const out = await v.run(await readBytes(report), signature);
    setPublicKey(out.key?.publicKey ?? null);
  };

  return (
    <form
      className="verify-grid"
      onSubmit={(e) => {
        e.preventDefault();
        void submit();
      }}
    >
      <div className="stack">
        <FilePick label="The report" hint="report.json, exactly as downloaded" accept=".json,application/json" file={report} onFile={(f) => (setReport(f), v.reset())} />
        <FilePick label="Its signature" hint="report.json.asc" accept=".asc,.sig,text/plain" file={sigFile} onFile={(f) => (setSigFile(f), v.reset())} />
        {!sigFile ? (
          <div className="field">
            <label htmlFor="pasted-sig">Or paste the signature</label>
            <textarea
              id="pasted-sig"
              className="textarea mono"
              rows={5}
              placeholder="-----BEGIN PGP SIGNATURE-----"
              value={pasted}
              onChange={(e) => (setPasted(e.target.value), v.reset())}
              spellCheck={false}
            />
          </div>
        ) : null}
        {problem ? (
          <p className="error-text" role="alert">
            {problem}
          </p>
        ) : null}
        <div className="cluster">
          <button className="btn btn-primary" type="submit" disabled={v.busy}>
            {v.busy ? <span className="spinner" aria-hidden="true" /> : null}
            {v.busy ? "Checking…" : "Verify the report"}
          </button>
        </div>
        <p className="faint small">Both files stay on this device. Only the report&rsquo;s SHA-256 fingerprint is looked up on the ledger.</p>
      </div>
      <div className="panel stack" aria-live="polite">
        {v.steps ? (
          <>
            {v.outcome ? <VerifyOutcomeView outcome={v.outcome} /> : <h2 style={{ fontSize: "var(--step-1)" }}>Checking…</h2>}
            <VerifySteps steps={v.steps} />
            {v.outcome ? <OfflineCheck publicKey={publicKey} /> : null}
          </>
        ) : (
          <div className="stack-s stack">
            <h2 style={{ fontSize: "var(--step-1)" }}>What gets checked</h2>
            <ol className="sequence">
              <li>The validator&rsquo;s public key, as a Revenue officer attested it on the ledger.</li>
              <li>The OpenPGP signature over the exact bytes of the report.</li>
              <li>That the key which signed is that attested key.</li>
              <li>That the report&rsquo;s SHA-256 fingerprint is anchored on the ledger by the same key.</li>
            </ol>
          </div>
        )}
      </div>
    </form>
  );
}

type DocResult = Awaited<ReturnType<typeof verifyDocument>>;

function DocumentForm() {
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<DocResult | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const submit = async () => {
    setProblem(null);
    setResult(null);
    if (!file) return setProblem("Choose the document file.");
    setBusy(true);
    try {
      setResult(await verifyDocument(await readBytes(file)));
    } catch (e) {
      setProblem((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <form
      className="verify-grid"
      onSubmit={(e) => {
        e.preventDefault();
        void submit();
      }}
    >
      <div className="stack">
        <FilePick
          label="The document"
          hint="A sale deed or encumbrance certificate saved from DigiLocker (the citizen dashboard can download one)"
          file={file}
          onFile={(f) => (setFile(f), setResult(null))}
        />
        {problem ? (
          <p className="error-text" role="alert">
            {problem}
          </p>
        ) : null}
        <div className="cluster">
          <button className="btn btn-primary" type="submit" disabled={busy}>
            {busy ? <span className="spinner" aria-hidden="true" /> : null}
            {busy ? "Checking…" : "Check the document"}
          </button>
        </div>
        <p className="faint small">The file is fingerprinted (SHA-256) on this device; only the fingerprint is looked up.</p>
      </div>
      <div className="panel stack" aria-live="polite">
        {result ? (
          result.anchored ? (
            <>
              <div className="verify-stamp">
                <Stamp tone="ok" sub="This exact file is anchored on the ledger">
                  ANCHORED
                </Stamp>
              </div>
              <dl className="kv">
                <dt>Document</dt>
                <dd>
                  {docType(result.documentType)} <span className="mono">{result.docId}</span>
                </dd>
                <dt>Issued by</dt>
                <dd>{result.issuer}</dd>
                <dt>Filed with</dt>
                <dd className="mono">{result.appId}</dd>
                <dt>Anchored by</dt>
                <dd>
                  {result.anchoredBy}
                  {result.anchoredAt ? `, ${when(result.anchoredAt)}` : ""}
                </dd>
                <dt>SHA-256</dt>
                <dd>
                  <Hash value={result.digest} full />
                </dd>
              </dl>
            </>
          ) : (
            <>
              <div className="verify-stamp">
                <Stamp tone="bad" sub="No anchor has this fingerprint">
                  NOT ON THE LEDGER
                </Stamp>
              </div>
              <ul className="checklist">
                <li>
                  <Mark state="fail" />
                  <div>
                    <div className="title">Fingerprint not found</div>
                    <div className="detail">
                      SHA-256 <Hash value={result.digest} /> was never anchored. Either this document was not filed with
                      a transfer, or even one character of it differs from the copy that was.
                    </div>
                  </div>
                </li>
              </ul>
            </>
          )
        ) : (
          <div className="stack-s stack">
            <h2 style={{ fontSize: "var(--step-1)" }}>How a document is checked</h2>
            <p className="muted">
              When Registration files a sale, it anchors each document&rsquo;s SHA-256 fingerprint on the ledger. Any
              copy with the same fingerprint is byte-for-byte the document that was filed.
            </p>
          </div>
        )}
      </div>
    </form>
  );
}


/** A downloaded transfer record (public or party copy): its signature, its signer, and its file on the ledger. */
function RecordForm() {
  const [file, setFile] = useState<File | null>(null);
  const [pasted, setPasted] = useState("");
  const [busy, setBusy] = useState(false);
  const [out, setOut] = useState<RecordCheck | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const run = async () => {
    setProblem(null);
    setOut(null);
    const text = file ? await file.text() : pasted;
    if (!text.includes("BEGIN PGP SIGNED MESSAGE")) {
      setProblem("That is not a LandChain transfer record: it should start with -----BEGIN PGP SIGNED MESSAGE-----.");
      return;
    }
    setBusy(true);
    try {
      setOut(await verifyRecord(text));
    } catch (e) {
      setProblem((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="grid-2">
      <div className="panel stack">
        <FilePick label="The record" hint="landchain-APP-…-public.asc or your party copy, exactly as downloaded"
          accept=".asc,.txt,text/plain" file={file} onFile={(f) => (setFile(f), setOut(null))} />
        <div className="field">
          <label htmlFor="rec-paste">Or paste it</label>
          <textarea id="rec-paste" className="input input-mono" rows={5} value={pasted} onChange={(e) => setPasted(e.target.value)}
            placeholder="-----BEGIN PGP SIGNED MESSAGE-----" />
        </div>
        <div className="cluster">
          <button type="button" className="btn btn-primary" onClick={() => void run()} disabled={busy || (!file && !pasted.trim())}>
            {busy ? "Checking…" : "Verify the record"}
          </button>
        </div>
        {problem ? <p className="error-text" role="alert">{problem}</p> : null}
      </div>
      <div className="panel stack">
        <h3 style={{ fontSize: "var(--step-1)" }}>What gets checked</h3>
        <ol className="small">
          <li>The Records Office key, and that the Collector attested its fingerprint on the ledger.</li>
          <li>The OpenPGP signature over the record’s text, in this browser (OpenPGP.js).</li>
          <li>That the record was signed by that attested key.</li>
          <li>That the transfer file it describes exists on the ledger, and its status now.</li>
        </ol>
        {out ? (
          <div className="stack-s" role="status">
            <Stamp tone={out.valid ? "ok" : "bad"} sub={out.recordId ?? ""}>{out.valid ? "VERIFIED" : "NOT VALID"}</Stamp>
            <ul className="small">
              {out.steps.map((s) => (
                <li key={s.label}>{s.ok === null ? "–" : s.ok ? "✓" : "✗"} {s.label}{s.detail ? `: ${s.detail}` : ""}</li>
              ))}
            </ul>
            {out.issuedAt ? <p className="small muted">{out.copy} copy issued {when(out.issuedAt)}{out.issuedTo ? ` to ${out.issuedTo}` : ""}.</p> : null}
          </div>
        ) : (
          <p className="small muted">
            Offline, with GnuPG: import the Records Office key (<a href="/gw/api/public/records-key">shown here</a>) and run{" "}
            <code>gpg --verify landchain-APP-…-public.asc</code>.
          </p>
        )}
      </div>
    </div>
  );
}
