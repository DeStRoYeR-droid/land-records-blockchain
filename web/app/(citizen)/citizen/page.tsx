"use client";

import Link from "next/link";

import { StatusBadge, ParcelBadge } from "@/components/ui/badges";
import { EmptyState, ErrorState } from "@/components/ui/states";
import { Loading, SkeletonPanel } from "@/components/ui/skeleton";
import { TimeAgo } from "@/components/ui/time";
import { area, rupees } from "@/lib/format";
import { usePortal } from "@/lib/portal";

export default function MyLand() {
  const { session, me } = usePortal();
  const d = me.data;
  const incoming = (d?.intents ?? []).filter((i) => i.status === "awaiting-buyer" && i.buyerDigilockerId === session.citizen.digilockerId);
  return (
    <div className="stack-l">
      <header className="dash-head">
        <div>
          <h1>My land</h1>
          <p>Land registered in your name on the ledger, and the sales you are part of.</p>
        </div>
        <Link className="btn btn-primary" href="/citizen/sell">
          Sell land
        </Link>
      </header>

      {incoming.length ? (
        <div className="callout" data-tone="col" role="status">
          <strong>{incoming.length === 1 ? "Someone wants to sell you land." : `${incoming.length} people want to sell you land.`}</strong> <Link href="/citizen/transfers">Review and sign</Link>
        </div>
      ) : null}

      {me.error && !d ? (
        <ErrorState error={me.error} onRetry={me.reload} />
      ) : !d ? (
        <>
          <SkeletonPanel height="8rem" />
          <Loading label="Loading your land" />
        </>
      ) : (
        <>
          <section aria-labelledby="h-parcels">
            <h2 id="h-parcels" style={{ fontSize: "var(--step-2)", marginBottom: "var(--s-4)" }}>
              Parcels
            </h2>
            {d.parcels.length === 0 ? (
              <EmptyState title="No land is registered in your name" headingLevel={3}>
                <p>When the Sub-Registrar records a parcel for you, or a purchase is approved, it appears here.</p>
              </EmptyState>
            ) : (
              <div className="grid-2">
                {d.parcels.map((p) => (
                  <article className="panel stack-s stack" key={p.surveyNo}>
                    <div className="panel-head" style={{ marginBottom: 0 }}>
                      <h3 className="survey-no">{p.surveyNo}</h3>
                      <ParcelBadge status={p.status} />
                    </div>
                    <p>
                      {p.village}, {p.district} · {area(p.areaSqm)}
                    </p>
                    {p.pendingApplication ? (
                      <p className="small">
                        Transfer <Link className="mono" href={`/citizen/files/${p.pendingApplication}`}>{p.pendingApplication}</Link> is under way.
                      </p>
                    ) : null}
                  </article>
                ))}
              </div>
            )}
          </section>

          <section aria-labelledby="h-files">
            <h2 id="h-files" style={{ fontSize: "var(--step-2)", marginBottom: "var(--s-4)" }}>
              Files
            </h2>
            {d.applications.length === 0 ? (
              <p className="muted">You are not part of any transfer file.</p>
            ) : (
              <div className="panel panel-flush">
                <div className="table-wrap">
                  <table className="table">
                    <caption className="visually-hidden">Your transfer files</caption>
                    <thead>
                      <tr>
                        <th scope="col">File</th>
                        <th scope="col">You are</th>
                        <th scope="col">With</th>
                        <th scope="col" className="right">
                          Price
                        </th>
                        <th scope="col">Status</th>
                        <th scope="col">Updated</th>
                      </tr>
                    </thead>
                    <tbody>
                      {d.applications.map((a) => (
                        <tr key={a.appId}>
                          <td>
                            <Link className="row-link mono" href={`/citizen/files/${a.appId}`}>
                              {a.appId}
                            </Link>
                            <div className="small survey-no muted">{a.surveyNo}</div>
                          </td>
                          <td>{a.myRole === "seller" ? "Seller" : "Buyer"}</td>
                          <td>{a.myRole === "seller" ? a.buyerName : a.sellerName}</td>
                          <td className="right num">{rupees(a.considerationINR)}</td>
                          <td>
                            <StatusBadge status={a.status} />
                          </td>
                          <td>
                            <TimeAgo value={a.updatedAt} />
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            )}
          </section>
        </>
      )}
    </div>
  );
}
