import { AgentConsole } from "@/components/agent/console";
import { Fingerprint } from "@/components/ui/hash";
import { pageMetadata } from "@/lib/metadata";
import { number } from "@/lib/format";
import { gwMaybe } from "@/lib/server";
import type { AgentPublic } from "@/lib/types";

export const dynamic = "force-dynamic";

export const metadata = pageMetadata({
  title: "The AI validator",
  description:
    "How LandChain's AI validator reads each transfer file, measures its own explanations for hallucination, signs a report with OpenPGP and anchors it on the ledger.",
  path: "/agent",
});

export default async function AgentPage() {
  const a = await gwMaybe<AgentPublic>("/api/public/agent");
  const c = a?.counters;
  return (
    <div className="page">
      <header className="page-head">
        <h1>The AI validator</h1>
        <p>
          It reads every transfer file and runs fifteen fixed checks. Then a small local language model investigates: it chooses read-only tools, reads what they return, and reports findings that are kept only if the results support them. It writes two explanations, each measured against the checked facts, and signs a report. It can never approve, reject or change a record.
        </p>
      </header>

      <section className="panel stack" aria-labelledby="h-public" style={{ marginBottom: "var(--s-6)" }}>
        <div className="panel-head">
          <h2 id="h-public">Public status</h2>
          <span className="badge" data-tone={a?.online ? "ok" : "bad"}>
            {a?.online ? "Online" : "Offline"}
          </span>
        </div>
        {a?.online ? (
          <dl className="kv">
            <dt>Signing key</dt>
            <dd>{a.pgpFingerprint ? <Fingerprint value={a.pgpFingerprint} /> : "—"}</dd>
            <dt>On the ledger</dt>
            <dd>{a.attested ? "Attested by a Revenue officer" : "Not attested yet: reports cannot be fully verified"}</dd>
            <dt>Writer</dt>
            <dd>
              {a.explainer?.engine === "omlx" ? `Language model ${a.explainer.model ?? ""} running locally` : "Rule-based template (the language model is offline)"}
            </dd>
            {a.framework ? (
              <>
                <dt>Built with</dt>
                <dd>
                  {a.framework.orchestration}; {a.framework.llm}; {a.framework.nodes}-node graph
                </dd>
              </>
            ) : null}
            {c ? (
              <>
                <dt>Reports so far</dt>
                <dd>
                  {number(c.runs)} runs: {number(c.clear)} clear, {number(c.attention)} needing attention, {number(c.flagged)} flagged
                  {c.failed ? `, ${number(c.failed)} failed` : ""}
                </dd>
              </>
            ) : null}
          </dl>
        ) : (
          <p className="muted">The validator is not answering. If you run LandChain, start it with <code>./startup.sh</code>. Reports already signed can still be verified.</p>
        )}
      </section>

      <AgentConsole />
    </div>
  );
}
