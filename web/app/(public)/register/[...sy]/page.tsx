import Link from "next/link";
import { notFound } from "next/navigation";

import { ParcelMap } from "@/components/map/parcel-map";
import { OwnershipChain } from "@/components/parcel/ownership-chain";
import { TransferRecords } from "@/components/parcel/transfer-records";
import { ParcelBadge } from "@/components/ui/badges";
import { Hash } from "@/components/ui/hash";
import { SmartImage } from "@/components/ui/smart-image";
import { area, day, number, parcelPath, plural, when } from "@/lib/format";
import { pageMetadata } from "@/lib/metadata";
import { gw, GatewayError } from "@/lib/server";
import type { Ownership, ParcelCollection, ParcelFeature, PublicParcel, PublicParcelEvent } from "@/lib/types";

export const dynamic = "force-dynamic";

type Props = { params: Promise<{ sy: string[] }> };
type Detail = { parcel: PublicParcel; events: PublicParcelEvent[]; ownership: Ownership | null };
type Geo = { parcel: ParcelFeature; neighbours: ParcelCollection };

/** Survey numbers keep their slash in the URL: /register/SY-142-12/4A -> ["SY-142-12", "4A"]. */
function surveyFrom(segments: string[]): string {
  return segments
    .map((s) => {
      try {
        return decodeURIComponent(s);
      } catch {
        return s;
      }
    })
    .join("/");
}

async function load(surveyNo: string): Promise<Detail | "offline" | null> {
  try {
    return await gw<Detail>(`/api/public/parcel?surveyNo=${encodeURIComponent(surveyNo)}`);
  } catch (e) {
    if (e instanceof GatewayError && e.status === 404) return null;
    return "offline";
  }
}

export async function generateMetadata({ params }: Props) {
  const surveyNo = surveyFrom((await params).sy);
  const data = await load(surveyNo);
  if (data === null) notFound(); // here as well as in the page, so crawlers get a real 404 status
  const p = data !== "offline" ? data.parcel : null;
  return pageMetadata({
    title: p ? `Survey ${p.surveyNo}, ${p.village}` : `Survey ${surveyNo}`,
    description: p
      ? `Parcel ${p.surveyNo} in ${p.village}, ${p.district}: ${area(p.areaSqm)}, ${p.status === "ACTIVE" ? "registered" : "transfer under way"}. Its record on the LandChain ledger.`
      : `Parcel ${surveyNo} on the LandChain land register.`,
    path: parcelPath(surveyNo),
    image: { url: `/og/parcel?surveyNo=${encodeURIComponent(surveyNo)}`, alt: `Parcel ${surveyNo} on the LandChain register` },
  });
}

const EVENT_TEXT: Record<PublicParcelEvent["kind"], string> = {
  registered: "Entered in the digital register",
  pending: "Transfer application opened",
  transferred: "Ownership transferred by the Collector",
  released: "Transfer ended without a change of owner",
};

export default async function ParcelPage({ params }: Props) {
  const surveyNo = surveyFrom((await params).sy);
  const data = await load(surveyNo);
  if (data === null) notFound();
  const [geo, overlaps] = data !== "offline" && data.parcel.boundary
    ? await Promise.all([
        gw<Geo>(`/api/public/geo/parcel?surveyNo=${encodeURIComponent(surveyNo)}&within=1`).catch(() => null),
        gw<ParcelCollection>(`/api/public/geo/overlaps?surveyNo=${encodeURIComponent(surveyNo)}`).catch(() => null),
      ])
    : [null, null];
  const overlapping = overlaps?.features ?? [];

  return (
    <div className="page">
      <nav className="crumbs" aria-label="Breadcrumb">
        <ol>
          <li>
            <Link href="/register">Land register</Link>
          </li>
          <li aria-current="page">{surveyNo}</li>
        </ol>
      </nav>
      {data === "offline" ? (
        <div className="error-state" role="alert" style={{ marginTop: "var(--s-5)" }}>
          <h1 style={{ fontSize: "var(--step-2)" }}>This parcel&rsquo;s record could not be read</h1>
          <p>The ledger did not answer. Reload the page in a moment; if you run LandChain, check ./landchain.sh status.</p>
        </div>
      ) : (
        <article className="parcel-page">
          <header className="page-head" style={{ marginTop: "var(--s-4)" }}>
            <h1 className="survey-no" style={{ fontSize: "var(--step-4)" }}>
              {data.parcel.surveyNo}
            </h1>
            <p>
              {data.parcel.village}, {data.parcel.district} district. <ParcelBadge status={data.parcel.status} />
            </p>
          </header>
          <div className="grid-2">
            <section aria-labelledby="facts" className="panel">
              <h2 id="facts">On the register</h2>
              <dl className="kv" style={{ marginTop: "var(--s-4)" }}>
                <dt>Extent</dt>
                <dd>{area(data.parcel.areaSqm)}</dd>
                {data.parcel.boundaryAreaSqm ? (
                  <>
                    <dt>Surveyed boundary</dt>
                    <dd>
                      {area(data.parcel.boundaryAreaSqm)}, perimeter {number(Math.round(data.parcel.perimeterM ?? 0))} m
                    </dd>
                    <dt>Location</dt>
                    <dd className="num">
                      {data.parcel.centroid?.[1].toFixed(5)}° N, {data.parcel.centroid?.[0].toFixed(5)}° E
                    </dd>
                  </>
                ) : null}
                <dt>Village</dt>
                <dd>{data.parcel.village}</dd>
                <dt>District</dt>
                <dd>{data.parcel.district}</dd>
                <dt>Owner</dt>
                <dd>
                  {data.parcel.ownerInitials || "—"} <span className="faint small">(initials only on the public register)</span>
                </dd>
                <dt>Registered</dt>
                <dd>{day(data.parcel.registeredAt)}</dd>
                <dt>Last transfer</dt>
                <dd>{data.parcel.lastTransferAt ? day(data.parcel.lastTransferAt) : "None since registration"}</dd>
                <dt>Owners so far</dt>
                <dd>{data.ownership ? data.ownership.owners : "—"}</dd>
              </dl>
            </section>
            {geo ? (
              <div className="stack-s">
                <ParcelMap parcel={geo.parcel} neighbours={geo.neighbours.features} warn={overlapping.map((f) => f.id)} />
                {overlapping.length ? (
                  <p className="callout" data-tone="warn" role="note">
                    The geospatial database finds {plural(overlapping.length, "registered parcel")} whose boundary overlaps
                    this one: {overlapping.map((f) => `${f.id} (${number(f.properties.overlapSqm ?? 0)} sq.m)`).join(", ")}.
                    The same ground is recorded twice; the departments review such cases.
                  </p>
                ) : null}
                <p className="faint small">
                  The boundary recorded on the ledger
                  {geo.neighbours.features.length
                    ? `, with ${plural(geo.neighbours.features.length, "neighbouring parcel")} sharing ${number(Math.round(geo.neighbours.features.reduce((n, f) => n + (f.properties.sharedBoundaryM ?? 0), 0)))} m of boundary. Click a neighbour to open it.`
                    : "."}
                </p>
              </div>
            ) : (
            <figure className="stack-s" style={{ margin: 0 }}>
              <SmartImage
                src={`/gw/api/public/sketch?surveyNo=${encodeURIComponent(data.parcel.surveyNo)}`}
                alt={`Sketch of parcel ${data.parcel.surveyNo}`}
                width={400}
                height={300}
                fallbackText={`The sketch of ${data.parcel.surveyNo} could not be drawn just now.`}
              />
              <figcaption className="faint small">An illustrative sketch: no surveyed boundary is on the ledger for this parcel.</figcaption>
            </figure>
            )}
          </div>

          {data.ownership ? (
            <div className="grid-2 section" style={{ borderTop: 0, paddingTop: "var(--s-6)" }}>
              <section aria-labelledby="chain">
                <h2 id="chain">Chain of ownership</h2>
                <p className="muted" style={{ marginBottom: "var(--s-4)" }}>
                  Every owner the ledger has recorded, oldest first, derived from the parcel&rsquo;s committed versions.
                  Each change of hands is a Collector&rsquo;s approval in a numbered block.
                </p>
                <OwnershipChain chain={data.ownership} />
              </section>
              <section aria-labelledby="records">
                <h2 id="records">Transfers and their records</h2>
                <p className="muted" style={{ marginBottom: "var(--s-4)" }}>
                  Every transfer file opened on this parcel, approved, rejected or still in progress.
                </p>
                <TransferRecords transfers={data.ownership.transfers} />
              </section>
            </div>
          ) : null}

          <section aria-labelledby="history" className="section" style={{ borderTop: 0, paddingTop: "var(--s-6)" }}>
            <h2 id="history">History on the ledger</h2>
            <p className="muted" style={{ marginBottom: "var(--s-4)" }}>
              Each line is a committed transaction; its ID finds it in any department&rsquo;s copy of the ledger.
            </p>
            <ol className="trail">
              {data.events.map((e) => (
                <li key={e.txId} data-org={e.kind === "transferred" ? "Org3MSP" : "Org1MSP"}>
                  <span className="dot" aria-hidden="true" />
                  <span className="what">
                    {EVENT_TEXT[e.kind]}
                    {e.ownerInitials ? `: owner ${e.ownerInitials}` : ""}
                  </span>
                  <span className="meta">
                    <time dateTime={e.at}>{when(e.at)}</time>
                    <span>
                      Transaction <Hash value={e.txId} head={10} tail={4} />
                    </span>
                  </span>
                </li>
              ))}
            </ol>
          </section>
        </article>
      )}
    </div>
  );
}
