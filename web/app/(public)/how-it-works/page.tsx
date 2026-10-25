import Link from "next/link";

import { pageMetadata } from "@/lib/metadata";

export const metadata = pageMetadata({
  title: "How LandChain works",
  description:
    "How a land transfer moves through Registration, Revenue and the Collectorate on a shared ledger, and how an AI validator checks the file without ever deciding it.",
  path: "/how-it-works",
});

export default function HowItWorks() {
  return (
    <div className="page-narrow prose">
      <header className="page-head">
        <h1>How LandChain works</h1>
        <p>One shared record of who owns which parcel, kept by three departments and checked by a fourth, non-voting, party: an AI validator.</p>
      </header>

      <section aria-labelledby="h-people">
        <h2 id="h-people">Who does what</h2>
        <ol className="sequence">
          <li>
            <strong>The seller and the buyer</strong> sign a transfer intent with keys that never leave their browsers.
          </li>
          <li>
            <strong>Registration</strong> opens a file from the signed intent, anchors the seller&rsquo;s documents from
            DigiLocker (with the seller&rsquo;s one-time code) and forwards it.
          </li>
          <li>
            <strong>Revenue</strong> re-fetches the documents by itself and compares fingerprints, a surveyor measures
            the land, and the file goes on.
          </li>
          <li>
            <strong>The Collector</strong> approves or rejects. Only an approval changes the owner.
          </li>
        </ol>
        <p>
          Each step is a transaction that must be endorsed by the right departments before it is written. Nobody, not
          even an administrator, can edit a past block.
        </p>
      </section>

      <section aria-labelledby="h-geo">
        <h2 id="h-geo">The map and the chain of ownership</h2>
        <p>
          Every parcel carries its surveyed boundary on the ledger, as a polygon of longitude and latitude. The ledger
          checks that the boundary encloses the recorded area. A <strong>geospatial database</strong> is built from those
          records: an OGC GeoPackage (the SQLite format QGIS and GDAL open) with an R*Tree spatial index. It answers what a
          ledger cannot answer quickly: which parcels are in this part of the map, which lie within 500 m of a point,
          which parcel is under this spot, which share a boundary, and whether a new boundary overlaps one already
          registered. It can be deleted at any time and rebuilt from the ledger.
        </p>
        <p>
          The <strong>chain of ownership</strong> of a parcel is read from the ledger&rsquo;s history of that parcel: every
          owner, how they acquired it, and the block of the Collector&rsquo;s approval. Each transfer has a
          <strong> signed record</strong> to download: a public copy anyone can have, and a fuller copy for the seller and
          the buyer. Both are text files with an OpenPGP signature inside, by the Records Office key the Collector attests
          on the ledger.
        </p>
      </section>

      <section aria-labelledby="h-agent">
        <h2 id="h-agent">The validator</h2>
        <p>
          When a file reaches Revenue and again at the Collectorate, an AI agent reads it. It runs fifteen fixed checks
          (do the documents match, is the deed&rsquo;s extent the parcel&rsquo;s, do the names match, does another parcel&rsquo;s
          boundary overlap this one, does a document try to instruct an AI, and so on). Then a small language model running
          on the department&rsquo;s own machine <strong>investigates</strong>: it chooses read-only tools (sale prices of the
          neighbouring parcels, the parties&rsquo; other files, the chain of ownership, the land next door), reads what they
          return, and reports findings that cite those results. A finding whose figures are not in the results is refused.
          <strong> Rules</strong> turn the checks into a verdict; the model&rsquo;s grounded concerns can only ask for an officer&rsquo;s
          attention. The model also writes two plain-language explanations, one for officers and one for citizens.
        </p>
        <p>
          Every sentence the model writes is measured against the checked facts. A number, name or date that is not in the
          facts counts as a hallucination, a sentence that claims a decision counts as one, and a text that scores below
          the threshold goes back to the model, which reads the complaint and chooses to redraft it once or hand over to a
          template. The result is a signed report, a plain-text
          certificate, and a trace of everything the agent did.
        </p>
        <p>
          The agent can only anchor its own report on the ledger. It cannot approve, reject or change a record: the
          decision stays with the Collector.
        </p>
      </section>

      <section aria-labelledby="h-trust">
        <h2 id="h-trust">Why you can check it yourself</h2>
        <ul>
          <li>The report is signed with OpenPGP. <code>gpg --verify report.json.asc report.json</code> works with no LandChain software.</li>
          <li>The signing key&rsquo;s fingerprint was attested on the ledger by a Revenue officer.</li>
          <li>The report&rsquo;s SHA-256 is anchored on the ledger by that key.</li>
          <li>An independent verifier re-derives the verdict and re-measures the explanations before the report is anchored. A report that fails is quarantined, not anchored.</li>
        </ul>
        <p>
          <Link className="btn btn-primary" href="/verify">
            Verify a report
          </Link>{" "}
          <Link className="btn" href="/agent">
            See the validator at work
          </Link>
        </p>
      </section>
    </div>
  );
}
