"use client";

import Link from "next/link";
import { useParams } from "next/navigation";

import { ParcelMap } from "@/components/map/parcel-map";
import { OwnershipChain } from "@/components/parcel/ownership-chain";
import { TransferRecords } from "@/components/parcel/transfer-records";
import { ParcelBadge, StatusBadge } from "@/components/ui/badges";
import { Hash } from "@/components/ui/hash";
import { ErrorState } from "@/components/ui/states";
import { Loading, SkeletonPanel } from "@/components/ui/skeleton";
import { Timestamp } from "@/components/ui/time";
import { useDesk } from "@/lib/desk";
import { area, rupees } from "@/lib/format";
import type { Application, Ownership, Parcel, ParcelCollection, ParcelFeature } from "@/lib/types";
import { useApi } from "@/lib/useApi";

interface Version {
  txId: string;
  timestamp: string | null;
  isDelete: boolean;
  value: Parcel | null;
}

export default function ParcelPage() {
  const { token } = useDesk();
  const { sy } = useParams<{ sy: string[] }>();
  const surveyNo = (sy ?? []).map(decodeURIComponent).join("/");
  const res = useApi<{ parcel: Parcel; history: Version[]; applications: Application[]; ownership: Ownership }>(`/api/parcel?surveyNo=${encodeURIComponent(surveyNo)}`, token);
  const geo = useApi<{ parcel: ParcelFeature; neighbours: ParcelCollection }>(`/api/public/geo/parcel?surveyNo=${encodeURIComponent(surveyNo)}&within=1`, null);
  const overlaps = useApi<ParcelCollection>(`/api/public/geo/overlaps?surveyNo=${encodeURIComponent(surveyNo)}`, null);
  const p = res.data?.parcel;
  return (
    <div className="stack-l">
      <nav className="crumbs" aria-label="Breadcrumb">
        <Link href="/officials/parcels">Parcels</Link> <span aria-hidden="true">/</span> <span className="survey-no">{surveyNo}</span>
      </nav>
      {res.error ? (
        <ErrorState error={res.error} onRetry={res.reload} title={res.error.status === 404 ? "No such parcel" : undefined} />
      ) : !p || !res.data ? (
        <>
          <SkeletonPanel height="10rem" />
          <Loading label="Loading the parcel" />
        </>
      ) : (
        <>
          <header className="dash-head">
            <div>
              <h1 className="survey-no">{p.surveyNo}</h1>
              <p>
                {p.village}, {p.district} · {area(p.areaSqm)}
              </p>
            </div>
            <ParcelBadge status={p.status} />
          </header>
          {geo.data ? (
            <section className="panel" aria-labelledby="h-map">
              <div className="panel-head">
                <h2 id="h-map">Boundary</h2>
                <span className="muted small">
                  {area(p.boundaryAreaSqm ?? 0)} in {p.corners} corners · {p.centroid?.[1].toFixed(5)}° N, {p.centroid?.[0].toFixed(5)}° E
                </span>
              </div>
              <ParcelMap parcel={geo.data.parcel} neighbours={geo.data.neighbours.features} base="/officials/parcels"
                warn={(overlaps.data?.features ?? []).map((f) => f.id)} />
              {overlaps.data?.features.length ? (
                <p className="callout" data-tone="warn" style={{ marginTop: "var(--s-3)" }}>
                  Overlaps {overlaps.data.features.map((f) => `${f.id} by ${f.properties.overlapSqm} sq.m`).join(", ")}.
                </p>
              ) : null}
            </section>
          ) : null}
          <div className="dash-grid">
            <section className="panel" aria-labelledby="h-chain">
              <div className="panel-head">
                <h2 id="h-chain">Chain of ownership</h2>
                <span className="muted small">{res.data.ownership.owners} owner(s), oldest first</span>
              </div>
              <OwnershipChain chain={res.data.ownership} appHref={(id) => `/officials/applications/${id}`} />
              <details style={{ marginTop: "var(--s-4)" }}>
                <summary>Every committed version ({res.data.history.length})</summary>
              <ol className="trail">
                {res.data.history.map((h, i) => (
                  <li key={h.txId + i} data-org="Org1MSP">
                    <span className="dot" aria-hidden="true" />
                    <span className="what">
                      {h.value ? (
                        <>
                          Owner <strong>{h.value.ownerName}</strong>
                          {h.value.status === "TRANSFER_PENDING" ? <> — transfer {h.value.pendingApplication} under way</> : null}
                        </>
                      ) : (
                        "Deleted"
                      )}
                    </span>
                    <span className="meta">
                      <Timestamp value={h.timestamp} />
                      <Hash value={h.txId} head={8} tail={4} label="transaction" />
                    </span>
                  </li>
                ))}
              </ol>
              </details>
            </section>
            <aside className="stack">
              <section className="panel" aria-labelledby="h-now">
                <h2 id="h-now" style={{ fontSize: "var(--step-1)", marginBottom: "var(--s-3)" }}>
                  Now
                </h2>
                <dl className="kv">
                  <dt>Owner</dt>
                  <dd>{p.ownerName}</dd>
                  <dt>Registered</dt>
                  <dd>
                    <Timestamp value={p.registeredAt} /> by {p.registeredBy}
                  </dd>
                  <dt>Last transfer</dt>
                  <dd>{p.lastTransfer ? `${p.lastTransfer.fromName} → ${p.lastTransfer.toName}` : "None yet"}</dd>
                </dl>
              </section>
              <section className="panel" aria-labelledby="h-apps">
                <h2 id="h-apps" style={{ fontSize: "var(--step-1)", marginBottom: "var(--s-3)" }}>
                  Transfer files
                </h2>
                <TransferRecords transfers={res.data.ownership.transfers} appHref={(id) => `/officials/applications/${id}`} />
                {res.data.applications.length ? (
                  <ul className="decisions" style={{ marginTop: "var(--s-3)" }}>
                    {res.data.applications.map((a) => (
                      <li key={a.appId}>
                        <span className="mono">{a.appId}</span>
                        <StatusBadge status={a.status} />
                        <span className="small muted">{rupees(a.considerationINR)}</span>
                      </li>
                    ))}
                  </ul>
                ) : null}
              </section>
            </aside>
          </div>
        </>
      )}
    </div>
  );
}
