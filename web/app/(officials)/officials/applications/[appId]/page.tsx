"use client";

import Link from "next/link";
import { useParams } from "next/navigation";

import { ValidatorPanel } from "@/components/agent/validator-panel";
import { RouteBar, Trail } from "@/components/app/parts";
import { Actions } from "@/components/desk/actions";
import { RecordDownloads } from "@/components/parcel/record-downloads";
import { StatusBadge, ParcelBadge } from "@/components/ui/badges";
import { Hash } from "@/components/ui/hash";
import { ErrorState } from "@/components/ui/states";
import { Loading, SkeletonPanel } from "@/components/ui/skeleton";
import { Stamp } from "@/components/ui/stamp";
import { Timestamp } from "@/components/ui/time";
import { useDesk } from "@/lib/desk";
import { area, docType, parcelPath, rupees } from "@/lib/format";
import type { ApplicationDetail } from "@/lib/types";
import { useApi } from "@/lib/useApi";

export default function ApplicationPage() {
  const { session, token, summary } = useDesk();
  const { appId } = useParams<{ appId: string }>();
  const res = useApi<ApplicationDetail>(`/api/applications/${encodeURIComponent(appId)}`, token, { refreshMs: 6000 });
  const d = res.data;
  const a = d?.application;
  const role = session.user.role;
  const changed = () => {
    res.reload();
    summary.reload();
  };
  return (
    <div className="stack-l">
      <nav className="crumbs" aria-label="Breadcrumb">
        <Link href="/officials/applications">Applications</Link> <span aria-hidden="true">/</span> <span className="mono">{appId}</span>
      </nav>
      {res.error && !d ? (
        <ErrorState error={res.error} onRetry={res.reload} title={res.error.status === 404 ? "No such application" : undefined} />
      ) : !d || !a ? (
        <>
          <SkeletonPanel height="6rem" />
          <SkeletonPanel height="14rem" />
          <Loading label="Loading the file" />
        </>
      ) : (
        <>
          <header className="dash-head">
            <div>
              <h1 className="mono">{a.appId}</h1>
              <p>
                Transfer of <Link className="survey-no" href={parcelPath(a.surveyNo, "/officials/parcels")}>{a.surveyNo}</Link> from {a.sellerName} to {a.buyerName}
              </p>
            </div>
            <div className="cluster">
              <StatusBadge status={a.status} />
            </div>
          </header>

          <RouteBar status={a.status} />

          {a.decision ? (
            <div className="cluster" style={{ paddingBlock: "var(--s-3)" }}>
              <Stamp tone={a.decision.outcome === "APPROVED" ? "ok" : "bad"} sub={<Timestamp value={a.decision.at} />}>
                {a.decision.outcome === "APPROVED" ? "APPROVED" : "REJECTED"}
              </Stamp>
              <div>
                <strong>{a.decision.by}</strong>
                <p className="muted">{a.decision.remarks || "No remarks recorded."}</p>
              </div>
            </div>
          ) : null}

          <div className="dash-grid">
            <div className="stack-l">
              <section className="panel" aria-labelledby="h-file">
                <div className="panel-head">
                  <h2 id="h-file">The file</h2>
                  <span className="small muted">{d.versions} versions on the ledger</span>
                </div>
                <dl className="kv">
                  <dt>Seller</dt>
                  <dd>{a.sellerName}</dd>
                  <dt>Buyer</dt>
                  <dd>{a.buyerName}</dd>
                  <dt>Consideration</dt>
                  <dd>{rupees(a.considerationINR)}</dd>
                  <dt>Parcel</dt>
                  <dd>
                    {d.parcel.village}, {d.parcel.district} · {area(d.parcel.areaSqm)} <ParcelBadge status={d.parcel.status} />
                  </dd>
                  <dt>Opened</dt>
                  <dd>
                    <Timestamp value={a.createdAt} /> by {a.createdBy}
                  </dd>
                  {a.consent ? (
                    <>
                      <dt>Consent</dt>
                      <dd>
                        Signed by both parties’ DIDs; intent <Hash value={a.consent.intentSha256} head={10} tail={6} label="intent fingerprint" />
                      </dd>
                    </>
                  ) : null}
                </dl>
              </section>

              <section className="panel" aria-labelledby="h-docs">
                <div className="panel-head">
                  <h2 id="h-docs">Documents</h2>
                  <span className="small muted">anchored by fingerprint</span>
                </div>
                {a.documents.length === 0 ? (
                  <p className="muted">No documents anchored yet.</p>
                ) : (
                  <ul className="doclist">
                    {a.documents.map((x) => (
                      <li key={x.sha256}>
                        <div>
                          <strong>{docType(x.documentType)}</strong> <span className="small muted">from {x.issuer}</span>
                          <div>
                            <Hash value={x.sha256} head={12} tail={8} label="document fingerprint" />
                          </div>
                        </div>
                      </li>
                    ))}
                  </ul>
                )}
                {a.verification ? (
                  <p className="callout" data-tone={a.verification.result === "MATCH" ? "ok" : "bad"} style={{ marginTop: "var(--s-3)" }}>
                    <strong>Revenue’s check:</strong> {a.verification.result === "MATCH" ? `all ${a.verification.documentsChecked} documents match.` : `${a.verification.mismatchedDocuments.length} document(s) differ from what was anchored.`}
                  </p>
                ) : null}
              </section>

              {a.survey ? (
                <section className="panel" aria-labelledby="h-survey">
                  <h2 id="h-survey" style={{ fontSize: "var(--step-1)", marginBottom: "var(--s-3)" }}>
                    Field survey
                  </h2>
                  <dl className="kv">
                    <dt>Recorded</dt>
                    <dd>{area(a.survey.recordedAreaSqm)}</dd>
                    <dt>Measured</dt>
                    <dd>{area(a.survey.measuredAreaSqm)}</dd>
                    <dt>Deviation</dt>
                    <dd>
                      {a.survey.deviationPct.toFixed(2)}% (tolerance {a.survey.tolerancePct}%) <span className="badge" data-tone={a.survey.withinTolerance ? "ok" : "bad"}>{a.survey.withinTolerance ? "Within" : "Outside"}</span>
                    </dd>
                    <dt>Boundaries</dt>
                    <dd>{a.survey.boundaries}</dd>
                    <dt>Surveyed by</dt>
                    <dd>
                      {a.survey.surveyedBy}, <Timestamp value={a.survey.at} />
                    </dd>
                  </dl>
                </section>
              ) : null}

              <ValidatorPanel runs={d.agentRuns} token={token} appId={a.appId} canRevalidate={role === "verifier" || role === "collector"} onChange={changed} />
            </div>

            <aside className="stack">
              <Actions app={a} parcel={d.parcel} role={role} token={token} onDone={changed} />
              <section className="panel" aria-labelledby="h-trail">
                <h2 id="h-trail" style={{ fontSize: "var(--step-1)", marginBottom: "var(--s-4)" }}>
                  Movement register
                </h2>
                <Trail entries={a.trail} />
              </section>
              <RecordDownloads appId={a.appId} />
            </aside>
          </div>
        </>
      )}
    </div>
  );
}
