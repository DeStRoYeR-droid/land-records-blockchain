"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useState } from "react";

import { RouteBar, Trail, ChecksList, Score } from "@/components/app/parts";
import { RecordDownloads } from "@/components/parcel/record-downloads";
import { StatusBadge, VerdictBadge } from "@/components/ui/badges";
import { Hash } from "@/components/ui/hash";
import { IconDownload } from "@/components/ui/icons";
import { EmptyState, ErrorState } from "@/components/ui/states";
import { Loading, SkeletonPanel } from "@/components/ui/skeleton";
import { Stamp } from "@/components/ui/stamp";
import { Timestamp } from "@/components/ui/time";
import { OfflineCheck, useReportVerifier, VerifyOutcomeView, VerifySteps } from "@/components/verify/report-verifier";
import { apiBytes, download, errorMessage } from "@/lib/api";
import { rupees, VERDICT_INFO } from "@/lib/format";
import { usePortal } from "@/lib/portal";
import type { CitizenReport } from "@/lib/types";
import { useApi } from "@/lib/useApi";

function ReportCard({ r, token, appId }: { r: CitizenReport; token: string; appId: string }) {
  const v = useReportVerifier();
  const [publicKey, setPublicKey] = useState<string | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const base = `/api/agent/runs/${r.runId}`;

  const check = async () => {
    setProblem(null);
    try {
      const [report, sig] = await Promise.all([apiBytes(`${base}/report.json`, token), apiBytes(`${base}/report.json.asc`, token)]);
      const out = await v.run(report, new TextDecoder().decode(sig));
      setPublicKey(out.key?.publicKey ?? null);
    } catch (e) {
      setProblem(errorMessage(e));
    }
  };
  const get = async (file: string) => {
    try {
      download(file, await apiBytes(`${base}/${file}`, token), file.endsWith(".json") ? "application/json" : "text/plain");
    } catch (e) {
      setProblem(errorMessage(e));
    }
  };

  return (
    <article className="panel stack" aria-label={`Validator report ${r.runId}`}>
      <div className="panel-head" style={{ marginBottom: 0 }}>
        <h3>The validator’s report</h3>
        <div className="cluster">
          <VerdictBadge verdict={r.verdict?.status} flags={r.verdict?.flagCount} />
          <span className="small muted">
            <Timestamp value={r.finishedAt} />
          </span>
        </div>
      </div>
      {r.verdict ? <p className="muted">{VERDICT_INFO[r.verdict.status].plain}</p> : null}
      {r.citizenExplanation ? (
        <blockquote className="explanation" data-audience="citizen">
          {r.citizenExplanation}
        </blockquote>
      ) : null}
      <p className="small muted">
        This report is evidence for the officers. It does not approve or reject anything.
        {r.citizenWriter?.engine === "omlx" ? ` The explanation was written by a language model (${r.citizenWriter.model}) and checked against the facts` : " The explanation was written from a template"}
        {r.citizenWriter?.faithfulness != null ? <> (faithfulness <Score value={r.citizenWriter.faithfulness} label="Faithfulness" />)</> : null}.
      </p>
      <div className="cluster">
        {r.verification?.independent != null ? (
          <span className="badge" data-tone={r.verification.independent ? "ok" : "bad"}>
            Independent verifier: {r.verification.independent ? "passed" : "failed"}
          </span>
        ) : null}
        {r.verification?.ledger != null ? (
          <span className="badge" data-tone={r.verification.ledger ? "ok" : "bad"}>
            Read back from the ledger: {r.verification.ledger ? "matches" : "differs"}
          </span>
        ) : null}
      </div>

      <details>
        <summary>What was checked ({r.checks.length})</summary>
        <div style={{ marginTop: "var(--s-3)" }}>
          <ChecksList checks={r.checks.map((c, i) => ({ id: String(i), label: c.label, status: c.status, detail: c.detail, evidence: [] }))} />
        </div>
      </details>

      <div className="stack-s stack">
        <h4>Check the signature yourself</h4>
        <p className="small muted">
          The report is signed with OpenPGP and its fingerprint is on the ledger. Your browser can check both, without trusting this website.
        </p>
        <div className="cluster">
          <button type="button" className="btn btn-primary" onClick={() => void check()} disabled={v.busy}>
            {v.busy ? <span className="spinner" aria-hidden="true" /> : null}
            Verify in this browser
          </button>
          <button type="button" className="btn btn-small" onClick={() => void get("report.json")}>
            <IconDownload aria-hidden="true" /> report.json
          </button>
          <button type="button" className="btn btn-small" onClick={() => void get("report.json.asc")}>
            <IconDownload aria-hidden="true" /> report.json.asc
          </button>
          {r.hasCertificate ? (
            <button type="button" className="btn btn-small" onClick={() => void get("certificate.txt")}>
              <IconDownload aria-hidden="true" /> certificate.txt
            </button>
          ) : null}
        </div>
        {problem ? (
          <p className="error-text" role="alert">
            {problem}
          </p>
        ) : null}
        {v.steps ? (
          <div className="stack" aria-live="polite">
            {v.outcome ? <VerifyOutcomeView outcome={v.outcome} /> : null}
            <VerifySteps steps={v.steps} />
            {v.outcome ? <OfflineCheck publicKey={publicKey} /> : null}
          </div>
        ) : null}
        {r.report ? (
          <p className="small muted">
            Report fingerprint: <Hash value={r.report.sha256} head={14} tail={8} label="report SHA-256" /> {r.anchor ? <>anchored in block {r.anchor.block}</> : null}
          </p>
        ) : null}
      </div>
      <span className="visually-hidden">{appId}</span>
    </article>
  );
}

export default function CitizenFile() {
  const { appId } = useParams<{ appId: string }>();
  const { me, token } = usePortal();
  const app = me.data?.applications.find((a) => a.appId === appId);
  const reports = useApi<{ reports: CitizenReport[] }>(`/api/citizen/reports?appId=${encodeURIComponent(appId)}`, token, { refreshMs: 8000 });
  return (
    <div className="stack-l">
      <nav className="crumbs" aria-label="Breadcrumb">
        <Link href="/citizen">My land</Link> <span aria-hidden="true">/</span> <span className="mono">{appId}</span>
      </nav>
      {!me.data ? (
        me.error ? (
          <ErrorState error={me.error} onRetry={me.reload} />
        ) : (
          <>
            <SkeletonPanel height="8rem" />
            <Loading label="Loading the file" />
          </>
        )
      ) : !app ? (
        <EmptyState title="That file is not yours" action={<Link className="btn" href="/citizen">Back to my land</Link>}>
          <p>You can only see files where you are the seller or the buyer.</p>
        </EmptyState>
      ) : (
        <>
          <header className="dash-head">
            <div>
              <h1>
                <span className="survey-no">{app.surveyNo}</span>
              </h1>
              <p>
                {app.myRole === "seller" ? `Selling to ${app.buyerName}` : `Buying from ${app.sellerName}`} for {rupees(app.considerationINR)}
              </p>
            </div>
            <StatusBadge status={app.status} />
          </header>
          <RouteBar status={app.status} />
          {app.decision ? (
            <div className="cluster" style={{ paddingBlock: "var(--s-3)" }}>
              <Stamp tone={app.decision.outcome === "APPROVED" ? "ok" : "bad"} sub={<Timestamp value={app.decision.at} />}>
                {app.decision.outcome}
              </Stamp>
              {app.decision.remarks ? <p className="muted">{app.decision.remarks}</p> : null}
            </div>
          ) : null}
          <div className="dash-grid">
            <div className="stack-l">
              <RecordDownloads appId={appId} token={token} role={app.myRole} />
              {reports.error && !reports.data ? (
                <ErrorState error={reports.error} onRetry={reports.reload} headingLevel={3} />
              ) : !reports.data ? (
                <SkeletonPanel height="8rem" />
              ) : reports.data.reports.length === 0 ? (
                <section className="panel">
                  <h2 style={{ fontSize: "var(--step-1)", marginBottom: "var(--s-2)" }}>No validator report yet</h2>
                  <p className="muted">When the file reaches Revenue, an AI validator reads it and writes a signed report. It will appear here.</p>
                </section>
              ) : (
                reports.data.reports.map((r) => <ReportCard key={r.runId} r={r} token={token} appId={appId} />)
              )}
            </div>
            <aside className="panel" aria-labelledby="h-trail">
              <h2 id="h-trail" style={{ fontSize: "var(--step-1)", marginBottom: "var(--s-4)" }}>
                Where it has been
              </h2>
              <Trail entries={app.trail} />
            </aside>
          </div>
        </>
      )}
    </div>
  );
}
