import { Fingerprint } from "@/components/ui/hash";
import { VerifyForm } from "@/components/verify/verify-form";
import { pageMetadata } from "@/lib/metadata";
import { gwMaybe } from "@/lib/server";
import type { AgentPublic } from "@/lib/types";

export const dynamic = "force-dynamic";

export const metadata = pageMetadata({
  title: "Verify a report, a record or a document",
  description:
    "Check a LandChain validator report or a downloaded transfer record against its OpenPGP signature and the ledger, or check that a document is the one filed with a transfer. Runs in your browser.",
  path: "/verify",
});

export default async function VerifyPage() {
  const agent = await gwMaybe<AgentPublic>("/api/public/agent");
  return (
    <div className="page">
      <header className="page-head">
        <h1>Verify a report, a record or a document</h1>
        <p>
          You do not have to trust this website. A validator report is signed with OpenPGP and its fingerprint is
          written on the ledger; a transfer record carries the Records Office&rsquo;s signature inside it, by a key the
          Collector attested on the ledger. This page repeats those checks in your browser, and the same checks can be
          done with GnuPG alone.
        </p>
      </header>
      {agent?.pgpFingerprint ? (
        <aside className="callout" data-tone="col" style={{ marginBottom: "var(--s-5)" }} aria-label="The validator's key">
          <strong>The validator&rsquo;s signing key</strong>
          <div style={{ marginTop: "var(--s-2)" }}>
            <Fingerprint value={agent.pgpFingerprint} />
          </div>
          <p className="small muted" style={{ marginTop: "var(--s-2)" }}>
            {agent.attested
              ? "A Revenue officer attested this key on the ledger."
              : "This key has not been attested on the ledger yet, so reports cannot be fully verified."}
          </p>
        </aside>
      ) : null}
      <VerifyForm />
    </div>
  );
}
