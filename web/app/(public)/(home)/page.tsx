import Link from "next/link";

import { HomeMap } from "@/components/map/home-map";
import { VillageMap } from "@/components/map/village-map";
import { IconLens, IconOffice, IconPerson } from "@/components/ui/icons";
import { day, number, parcelPath } from "@/lib/format";
import { pageMetadata } from "@/lib/metadata";
import { gwMaybe } from "@/lib/server";
import { SITE_DESCRIPTION } from "@/lib/site";
import type { ParcelCollection, PublicParcel, PublicSummary } from "@/lib/types";

export const dynamic = "force-dynamic";

export const metadata = {
  ...pageMetadata({ title: "LandChain: Hanamkonda district land register", description: SITE_DESCRIPTION, path: "/" }),
  title: { absolute: "LandChain: Hanamkonda district land register" }, // no "| LandChain" suffix on the home page
};

const ROUTE = [
  {
    title: "Seller and buyer sign",
    text: "Both sign the same transfer intent with a key that never leaves their own browser.",
    seg: "var(--ink)",
  },
  {
    title: "Registration opens the file",
    text: "The clerk files the signed intent and anchors the sale deed and encumbrance certificate by fingerprint.",
    seg: "var(--reg)",
  },
  {
    title: "The validator checks",
    text: "At the handoff an AI validator runs fifteen checks, investigates the open leads with its own tools, and anchors a signed report: evidence, not a decision.",
    seg: "var(--agent)",
  },
  {
    title: "Revenue verifies and surveys",
    text: "Revenue fetches the documents again on its own, compares fingerprints and measures the land.",
    seg: "var(--rev)",
  },
  {
    title: "The Collector decides",
    text: "The only step that can move ownership. Every department's peer must endorse it.",
    seg: "var(--col)",
  },
];

export default async function Home() {
  const [summary, register, geo] = await Promise.all([
    gwMaybe<PublicSummary>("/api/public/summary"),
    gwMaybe<{ parcels: PublicParcel[] }>("/api/public/parcels"),
    gwMaybe<ParcelCollection>("/api/public/geo/parcels"),
  ]);
  const offline = !summary || !register;
  const agent = summary?.agent;
  const decided = (summary?.byStatus.APPROVED ?? 0) + (summary?.byStatus.REJECTED ?? 0);
  const inProgress = (summary?.applications ?? 0) - decided;

  return (
    <>
      <section className="page hero" aria-labelledby="hero-title">
        <div>
          <h1 id="hero-title">Land changes hands here only when every office signs.</h1>
          <p className="lede">
            Registration, Revenue and the Collectorate keep one shared ledger of every parcel in the district. A sale
            needs both owners&rsquo; own signatures, a signed check by an AI validator, and the Collector&rsquo;s
            decision.
          </p>
          <div className="cluster">
            <Link className="btn btn-primary btn-large" href="/register">
              Look up a parcel
            </Link>
            <Link className="btn btn-large" href="/verify">
              Verify a validator report
            </Link>
          </div>
        </div>
        {geo && geo.features.length ? (
          <HomeMap features={geo.features} height={summary?.height ?? null} />
        ) : (
          <VillageMap parcels={register?.parcels ?? []} height={summary?.height ?? null} offline={offline} />
        )}
      </section>

      <section className="section" aria-labelledby="route-title">
        <div className="page">
          <div className="section-head">
            <h2 id="route-title">How a sale moves through the offices</h2>
            <p>Each step is a transaction that all three departments&rsquo; peers must endorse before it is written.</p>
          </div>
          <ol className="route-steps">
            {ROUTE.map((r) => (
              <li key={r.title} style={{ ["--seg" as string]: r.seg }}>
                <h3>{r.title}</h3>
                <p>{r.text}</p>
              </li>
            ))}
          </ol>
        </div>
      </section>

      <section className="section" aria-labelledby="doors-title">
        <div className="page">
          <div className="section-head">
            <h2 id="doors-title">Three dashboards, one ledger</h2>
          </div>
          <div className="doors">
            <article className="door" style={{ ["--door" as string]: "var(--reg)" }}>
              <IconOffice className="door-art" />
              <h3>Department officers</h3>
              <p>
                The clerk, the Sub-Registrar, the Revenue verifier and surveyor, and the District Collector each see the
                files waiting at their desk, with every action signed by their own certificate.
              </p>
              <div className="cluster">
                <Link className="btn" href="/officials">
                  Open the officers&rsquo; desk
                </Link>
              </div>
            </article>
            <article className="door" style={{ ["--door" as string]: "var(--ink)" }}>
              <IconPerson className="door-art" />
              <h3>Citizens</h3>
              <p>
                Create your signing key, sign a sale or accept one, follow your file, and check the validator&rsquo;s
                signed report yourself, in your browser.
              </p>
              <div className="cluster">
                <Link className="btn" href="/citizen/register">
                  Register
                </Link>
                <Link className="btn btn-quiet" href="/citizen/sign-in">
                  Sign in
                </Link>
              </div>
            </article>
            <article className="door" style={{ ["--door" as string]: "var(--agent)" }}>
              <IconLens className="door-art" />
              <h3>The AI validator</h3>
              <p>
                Watch it investigate a file live: each ledger and DigiLocker call, the fifteen checks, the tools the model chooses, the explanation a
                local model writes, the signature and the anchor.
              </p>
              <div className="cluster">
                <Link className="btn" href="/agent">
                  Open the validator console
                </Link>
              </div>
            </article>
          </div>
        </div>
      </section>

      <section className="section" aria-labelledby="ledger-title">
        <div className="page grid-2">
          <div className="stack">
            <h2 id="ledger-title">On the ledger now</h2>
            {offline || !summary ? (
              <p className="muted">
                The ledger did not answer. If you are running LandChain, start the network with <code>./startup.sh</code>.
              </p>
            ) : (
              <div className="ledger-line">
                <dl>
                  <div>
                    <dt>Blocks</dt>
                    <dd>{number(summary.height)}</dd>
                  </div>
                  <div>
                    <dt>Parcels</dt>
                    <dd>{number(summary.parcels)}</dd>
                  </div>
                  <div>
                    <dt>Sales in progress</dt>
                    <dd>{number(Math.max(inProgress, 0))}</dd>
                  </div>
                  <div>
                    <dt>Sales decided</dt>
                    <dd>{number(decided)}</dd>
                  </div>
                  <div>
                    <dt>Validator reports</dt>
                    <dd>{number(summary.reports)}</dd>
                  </div>
                </dl>
              </div>
            )}
            {agent ? (
              <p className="muted">
                {agent.online
                  ? `The validator is online${agent.listening ? " and listening for handoffs" : ""}; it writes its explanations with ${
                      agent.explainer?.engine === "omlx" ? `${agent.explainer.model ?? "a local model"} through oMLX` : "its template writer"
                    }.`
                  : "The validator is offline; files still move, without its report."}
              </p>
            ) : null}
          </div>
          <div className="stack">
            <h2>Latest decisions</h2>
            {summary?.recentDecisions.length ? (
              <ul className="decisions">
                {summary.recentDecisions.map((d) => (
                  <li key={`${d.surveyNo}-${d.at}`}>
                    <Link className="survey-no" href={parcelPath(d.surveyNo)}>
                      {d.surveyNo}
                    </Link>
                    <span className="badge" data-tone={d.outcome === "APPROVED" ? "ok" : "bad"}>
                      {d.outcome === "APPROVED" ? "Approved" : "Rejected"}
                    </span>
                    <time dateTime={d.at} className="faint small">
                      {day(d.at)}
                    </time>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="muted">No sale has been decided yet.</p>
            )}
          </div>
        </div>
      </section>
    </>
  );
}
