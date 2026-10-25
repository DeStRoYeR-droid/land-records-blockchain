"use client";

import { Score } from "@/components/app/parts";
import { RunStatusBadge } from "@/components/agent/run-detail";
import { VerdictBadge } from "@/components/ui/badges";
import { EmptyState, ErrorState } from "@/components/ui/states";
import { Loading, SkeletonPanel } from "@/components/ui/skeleton";
import { TimeAgo } from "@/components/ui/time";
import { duration } from "@/lib/format";
import type { AgentMetrics, RunStatus } from "@/lib/types";
import { useApi } from "@/lib/useApi";

const pct = (v: number | null | undefined) => (v == null ? "—" : `${(v * 100).toFixed(0)}%`);
const two = (v: number | null | undefined) => (v == null ? "—" : v.toFixed(2));

export function MetricsTab({ token }: { token: string }) {
  const res = useApi<AgentMetrics>("/api/agent/metrics", token, { refreshMs: 10000 });
  const m = res.data;
  if (res.error && !m) return <ErrorState error={res.error} onRetry={res.reload} />;
  if (!m)
    return (
      <>
        <SkeletonPanel height="10rem" />
        <Loading label="Loading metrics" />
      </>
    );
  if (m.runsMeasured === 0)
    return (
      <EmptyState title="Nothing to measure yet">
        <p>Quality numbers appear after the validator has written its first explanation.</p>
      </EmptyState>
    );
  const maxCount = Math.max(1, ...m.faithfulness.histogram.map((b) => b.count));
  const maxLat = Math.max(1, ...Object.values(m.nodeLatencyMs).map((n) => n.avg));
  return (
    <div className="stack-l">
      <dl className="stat-row">
        <div>
          <dt>Runs measured</dt>
          <dd>{m.runsMeasured}</dd>
        </div>
        <div>
          <dt>Mean faithfulness</dt>
          <dd>{two(m.faithfulness.mean)}</dd>
        </div>
        <div>
          <dt>Lowest</dt>
          <dd>{two(m.faithfulness.min)}</dd>
        </div>
        <div>
          <dt>Unsupported claims</dt>
          <dd>{pct(m.hallucinationRate.mean)}</dd>
        </div>
        <div>
          <dt>Verifier passes</dt>
          <dd>{pct(m.verification.independentPassShare)}</dd>
        </div>
      </dl>

      <div className="grid-2">
        <section className="panel stack" aria-labelledby="h-hist">
          <h2 id="h-hist" style={{ fontSize: "var(--step-1)" }}>
            Faithfulness of accepted texts
          </h2>
          <p className="small muted">{m.texts} texts. Each bar counts texts in that score range; the threshold to be accepted is 0.80.</p>
          <ol className="histogram" aria-label="Faithfulness histogram">
            {m.faithfulness.histogram.map((b) => (
              <li key={b.from}>
                <span className="small num">
                  {b.from.toFixed(1)}–{b.to.toFixed(1)}
                </span>
                <span className="bar" aria-hidden="true">
                  <span style={{ width: `${(b.count / maxCount) * 100}%` }} />
                </span>
                <span className="small num">{b.count}</span>
              </li>
            ))}
          </ol>
          <p className="small muted">
            Officer texts average {two(m.faithfulness.officerMean)}; citizen texts {two(m.faithfulness.citizenMean)}.
          </p>
        </section>

        <section className="panel stack" aria-labelledby="h-writers">
          <h2 id="h-writers" style={{ fontSize: "var(--step-1)" }}>
            Who wrote the accepted texts
          </h2>
          <dl className="kv">
            <dt>Language model</dt>
            <dd>{m.writers.omlx}</dd>
            <dt>Template</dt>
            <dd>{m.writers.template}</dd>
            <dt>Model texts accepted</dt>
            <dd>{pct(m.writers.modelAcceptedShare)}</dd>
            <dt>Redrafts</dt>
            <dd>{m.retries}</dd>
            <dt>Fell back to template</dt>
            <dd>{m.fallbacks}</dd>
            <dt>Prompt-injection hits</dt>
            <dd>{m.promptInjectionHits}</dd>
            <dt>Judge agreement</dt>
            <dd>
              {m.judge.compared ? `${pct(m.judge.agreementShare)} of ${m.judge.compared}` : "no second opinions yet"}
            </dd>
            <dt>Model speed</dt>
            <dd>{m.model.avgTokensPerSecond ? `${m.model.avgTokensPerSecond} tok/s (median ${m.model.medianTokensPerSecond}, ${m.model.samples} samples)` : "—"}</dd>
            <dt>Quarantined reports</dt>
            <dd>{m.verification.quarantined}</dd>
            <dt>Confirmed on the ledger</dt>
            <dd>{m.verification.ledgerConfirmed}</dd>
          </dl>
        </section>
      </div>

      {m.agentic ? (
        <section className="panel stack" aria-labelledby="h-agentic">
          <h2 id="h-agentic" style={{ fontSize: "var(--step-1)" }}>The model’s own choices</h2>
          <p className="small muted">What the investigating model did with its tool budget, and how many of its findings survived the grounding check.</p>
          <dl className="kv">
            <dt>Investigations</dt>
            <dd>{m.agentic.investigations} ({m.agentic.toolCalls} tool calls, {m.agentic.avgToolCalls ?? "—"} per file)</dd>
            <dt>Refused calls</dt>
            <dd>{m.agentic.invalidCalls} (unknown tools, bad arguments, repeats)</dd>
            <dt>Tools it chose</dt>
            <dd>{Object.entries(m.agentic.toolUse).map(([k, v]) => `${k} ${v}`).join(" · ") || "—"}</dd>
            <dt>How it stopped</dt>
            <dd>{Object.entries(m.agentic.stops).map(([k, v]) => `${k} ${v}`).join(" · ") || "—"}</dd>
            <dt>Findings</dt>
            <dd>
              {m.agentic.findingsAccepted} grounded ({m.agentic.concerns} concerns), {m.agentic.findingsRefused} refused
              {m.agentic.groundedShare !== null ? ` · ${pct(m.agentic.groundedShare)} grounded` : ""}
            </dd>
            <dt>Files only the model raised</dt>
            <dd>{m.agentic.filesRaisedOnlyByAgent} (attention because of its concern alone)</dd>
            <dt>Reviews of rejected drafts</dt>
            <dd>
              {m.agentic.reflections}: redraft {m.agentic.reflectChoices.redraft}, template {m.agentic.reflectChoices.template}
              {" "}({m.agentic.reflectByModel} chosen by the model)
            </dd>
          </dl>
        </section>
      ) : null}

      <section className="panel stack" aria-labelledby="h-lat">
        <h2 id="h-lat" style={{ fontSize: "var(--step-1)" }}>
          Time in each stage
        </h2>
        <div className="timings">
          {Object.entries(m.nodeLatencyMs).map(([node, v]) => (
            <div key={node} className="timing">
              <span className="mono small">{node}</span>
              <span className="bar" aria-hidden="true">
                <span style={{ width: `${Math.max(2, (v.avg / maxLat) * 100)}%` }} />
              </span>
              <span className="small muted num">
                avg {duration(Math.round(v.avg))} · max {duration(v.max)}
              </span>
            </div>
          ))}
        </div>
      </section>

      <section className="panel panel-flush" aria-labelledby="h-recent-runs">
        <div className="panel-head">
          <h2 id="h-recent-runs">Recent runs</h2>
        </div>
        <div className="table-wrap">
          <table className="table">
            <caption className="visually-hidden">Recent runs</caption>
            <thead>
              <tr>
                <th scope="col">Application</th>
                <th scope="col">Status</th>
                <th scope="col">Verdict</th>
                <th scope="col">Faithfulness</th>
                <th scope="col" className="right">Redrafts</th>
                <th scope="col" className="right">Template</th>
                <th scope="col">When</th>
              </tr>
            </thead>
            <tbody>
              {m.recent.map((r) => (
                <tr key={r.runId}>
                  <td className="mono">{r.appId}</td>
                  <td>
                    <RunStatusBadge status={r.status as RunStatus} />
                  </td>
                  <td>
                    <VerdictBadge verdict={r.verdict} />
                  </td>
                  <td>
                    <Score value={r.minFaithfulness} label="Lowest faithfulness" />
                  </td>
                  <td className="right num">{r.retries}</td>
                  <td className="right num">{r.fallbacks}</td>
                  <td>
                    <TimeAgo value={r.at} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  );
}
