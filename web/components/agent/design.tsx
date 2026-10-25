"use client";

import { GraphView } from "@/components/agent/graph-view";
import { ErrorState } from "@/components/ui/states";
import { Loading, SkeletonPanel } from "@/components/ui/skeleton";
import { Fingerprint, Hash } from "@/components/ui/hash";
import type { AgentStatus, GraphDescription } from "@/lib/types";
import { useApi } from "@/lib/useApi";

export function DesignTab({ token }: { token: string }) {
  const graph = useApi<GraphDescription>("/api/agent/graph", token);
  const status = useApi<AgentStatus>("/api/agent/status", token, { refreshMs: 8000 });
  const knowledge = useApi<{ sections: { title: string; text: string }[] }>("/api/agent/knowledge", token);
  const s = status.data;
  const g = graph.data;
  return (
    <div className="stack-l">
      {status.error && !s ? (
        <ErrorState error={status.error} onRetry={status.reload} />
      ) : !s ? (
        <>
          <SkeletonPanel height="8rem" />
          <Loading label="Loading the validator's setup" />
        </>
      ) : (
        <div className="grid-2">
          <section className="panel stack" aria-labelledby="h-id">
            <h2 id="h-id" style={{ fontSize: "var(--step-1)" }}>
              Identity
            </h2>
            <dl className="kv">
              <dt>Name</dt>
              <dd>{s.identity.name}</dd>
              <dt>Software</dt>
              <dd className="small">{s.software}</dd>
              <dt>OpenPGP key</dt>
              <dd>
                <Fingerprint value={s.identity.pgpFingerprint} />
              </dd>
              <dt>DID</dt>
              <dd>
                <Hash value={s.identity.did} head={16} tail={6} label="DID" />
              </dd>
              <dt>Ledger role</dt>
              <dd>
                {s.identity.role} in {s.identity.mspId}: it may anchor its own report and nothing else
              </dd>
              <dt>Key on the ledger</dt>
              <dd>
                {s.onChainKey.status}
                {s.onChainKey.attestedBy ? `, attested by ${s.onChainKey.attestedBy}` : ""}
                {s.onChainKey.matchesLocalKey === false ? " — does not match the local key!" : ""}
              </dd>
            </dl>
          </section>
          <section className="panel stack" aria-labelledby="h-model">
            <h2 id="h-model" style={{ fontSize: "var(--step-1)" }}>
              Language model
            </h2>
            <dl className="kv">
              <dt>Engine</dt>
              <dd>
                <span className="badge" data-tone={s.explainer.engine === "omlx" ? "col" : "muted"}>
                  {s.explainer.engine === "omlx" ? "oMLX (local)" : "Template writer"}
                </span>{" "}
                {s.explainer.reachable ? "reachable" : "not reachable"}
              </dd>
              <dt>Model</dt>
              <dd>{s.explainer.model ?? "—"}</dd>
              <dt>Endpoint</dt>
              <dd className="mono small">{s.explainer.baseUrl}</dd>
              <dt>API key</dt>
              <dd>{s.explainer.apiKeyConfigured ? "configured (never shown)" : "not set"}</dd>
              <dt>Last latency</dt>
              <dd>{s.explainer.lastLatencyMs != null ? `${(s.explainer.lastLatencyMs / 1000).toFixed(1)} s` : "—"}</dd>
              {s.explainer.lastError ? (
                <>
                  <dt>Last error</dt>
                  <dd className="small">{s.explainer.lastError}</dd>
                </>
              ) : null}
            </dl>
            <p className="small muted">If the model is down the template writer takes over and the report says so. Nothing else changes.</p>
          </section>
          {s.framework ? (
            <section className="panel stack" aria-labelledby="h-fw">
              <h2 id="h-fw" style={{ fontSize: "var(--step-1)" }}>
                Framework
              </h2>
              <dl className="kv">
                <dt>Orchestration</dt>
                <dd>{s.framework.orchestration}</dd>
                <dt>LLM client</dt>
                <dd>{s.framework.llm}</dd>
                <dt>Checkpoints</dt>
                <dd>{s.framework.checkpointer}</dd>
                <dt>Graph nodes</dt>
                <dd>{s.framework.nodes}</dd>
                <dt>Acceptance threshold</dt>
                <dd>faithfulness ≥ {s.framework.faithfulnessMin}, {s.framework.draftAttempts} drafts</dd>
                <dt>Second opinion</dt>
                <dd>{s.framework.judge}</dd>
              </dl>
            </section>
          ) : null}
          <section className="panel stack" aria-labelledby="h-limits">
            <h2 id="h-limits" style={{ fontSize: "var(--step-1)" }}>
              Where it sits
            </h2>
            <p>{s.placement}</p>
            <p>{s.authority}</p>
          </section>
        </div>
      )}

      <section className="panel stack" aria-labelledby="h-graph">
        <h2 id="h-graph" style={{ fontSize: "var(--step-1)" }}>
          The graph{g ? ` (${g.name} v${g.version})` : ""}
        </h2>
        {graph.error && !g ? (
          <ErrorState error={graph.error} onRetry={graph.reload} headingLevel={3} />
        ) : !g ? (
          <SkeletonPanel height="10rem" />
        ) : (
          <>
            <GraphView graph={g} />
            <details>
              <summary>Edges ({g.edges.length}) and libraries</summary>
              <ul className="edge-list">
                {g.edges.map((e, i) => (
                  <li key={i} className="mono small">
                    {e.from} {e.conditional ? "⇢" : "→"} {e.to}
                  </li>
                ))}
              </ul>
              <dl className="kv" style={{ marginTop: "var(--s-3)" }}>
                {Object.entries(g.libraries).map(([k, v]) => (
                  <div key={k} style={{ display: "contents" }}>
                    <dt>{k}</dt>
                    <dd>{v}</dd>
                  </div>
                ))}
              </dl>
            </details>
            <details>
              <summary>Mermaid source</summary>
              <pre className="codeblock">{g.mermaid}</pre>
            </details>
            {g.subgraphs?.investigate ? (
              <div className="stack-s">
                <h3 style={{ fontSize: "var(--step-0)" }}>Inside “investigate”: the agent loop</h3>
                <p className="small muted">
                  A LangGraph of its own: <span className="mono">think</span> (the model asks for tools or finishes) and{" "}
                  <span className="mono">act</span> (each call checked against the tool’s schema, repeats refused, then run).
                  At most {g.subgraphs.investigate.maxCalls} calls and {g.subgraphs.investigate.maxTurns} turns. Tools:{" "}
                  {g.subgraphs.investigate.tools.join(", ")}.
                </p>
                <pre className="codeblock">{g.subgraphs.investigate.mermaid}</pre>
              </div>
            ) : null}
          </>
        )}
      </section>

      <section className="panel stack" aria-labelledby="h-know">
        <h2 id="h-know" style={{ fontSize: "var(--step-1)" }}>
          What the validator knows about LandChain
        </h2>
        <p className="small muted">These notes are the only background the “Ask” tab gives the model.</p>
        {knowledge.data?.sections.length ? (
          knowledge.data.sections.map((k) => (
            <details key={k.title}>
              <summary>{k.title}</summary>
              <div className="prose" style={{ whiteSpace: "pre-wrap", marginTop: "var(--s-2)" }}>
                {k.text}
              </div>
            </details>
          ))
        ) : (
          <p className="muted">{knowledge.loading ? "Loading…" : "No notes available."}</p>
        )}
      </section>
    </div>
  );
}
