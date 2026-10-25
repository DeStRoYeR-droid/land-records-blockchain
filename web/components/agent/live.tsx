"use client";

import { useEffect, useRef, useState } from "react";

import { GraphView } from "@/components/agent/graph-view";
import { InvestigationTimeline } from "@/components/agent/investigation";
import { Score } from "@/components/app/parts";
import { ErrorState } from "@/components/ui/states";
import { Loading, SkeletonPanel } from "@/components/ui/skeleton";
import { duration } from "@/lib/format";
import { subscribe } from "@/lib/sse";
import type { AgentCall, AgentEvent, AgentStatus, AgentTurn, GraphDescription } from "@/lib/types";
import { useApi } from "@/lib/useApi";

interface Watching {
  runId: string;
  appId: string;
  active: string | null;
  visited: string[];
  failed: string | null;
  finished: string | null;
  text: Record<"officer" | "citizen", string>;
  attempt: Record<"officer" | "citizen", number | string>;
  scores: { audience: string; attempt: number; faithfulness: number; passed: boolean; route: string }[];
  agent: { started: boolean; turns: AgentTurn[]; calls: AgentCall[]; stop?: string; accepted?: number; refused?: number; model?: string; protocol?: string };
}

interface Line {
  id: number;
  at: number;
  text: string;
  tone?: "ok" | "bad" | "warn";
}

const blank = (runId: string, appId: string): Watching => ({
  runId,
  appId,
  active: null,
  visited: [],
  failed: null,
  finished: null,
  text: { officer: "", citizen: "" },
  attempt: { officer: 0, citizen: 0 },
  scores: [],
  agent: { started: false, turns: [], calls: [] },
});

/** Live view: the graph lighting up node by node, the model's words arriving, and the measurer's scores. */
export function LiveTab({ token }: { token: string }) {
  const graph = useApi<GraphDescription>("/api/agent/graph", token);
  const status = useApi<AgentStatus>("/api/agent/status", token, { refreshMs: 5000 });
  const [watching, setWatching] = useState<Watching | null>(null);
  const [lines, setLines] = useState<Line[]>([]);
  const [conn, setConn] = useState<"connecting" | "open" | "retrying">("connecting");
  const nextId = useRef(1);

  useEffect(() => {
    const say = (text: string, tone?: Line["tone"]) =>
      setLines((l) => [{ id: nextId.current++, at: Date.now(), text, tone }, ...l].slice(0, 40));
    return subscribe("/api/agent/stream", {
      token,
      onStatus: setConn,
      onEvent: (raw) => {
        const ev = raw as AgentEvent;
        switch (ev.type) {
          case "run.queued":
            say(`Queued ${ev.appId} (${String(ev.trigger && (ev.trigger as { kind?: string }).kind)})`);
            break;
          case "run.started":
            setWatching(blank(String(ev.runId), String(ev.appId)));
            say(`Started ${ev.appId}${ev.resumed ? " (resumed after a restart)" : ""}`);
            break;
          case "node":
            setWatching((w) => {
              if (!w || w.runId !== ev.runId) return w;
              const node = String(ev.node);
              if (ev.state === "started") return { ...w, active: node };
              return { ...w, active: w.active === node ? null : w.active, visited: [...w.visited, node], failed: ev.error ? node : w.failed };
            });
            if (ev.state === "finished") say(`${ev.node} · ${duration(Number(ev.ms))}`, ev.error ? "bad" : undefined);
            break;
          case "explain.started":
            setWatching((w) => {
              if (!w || w.runId !== ev.runId) return w;
              const a = ev.audience as "officer" | "citizen";
              return { ...w, text: { ...w.text, [a]: "" }, attempt: { ...w.attempt, [a]: Number(ev.attempt) } };
            });
            say(`The model starts the ${ev.audience} text (attempt ${ev.attempt})`);
            break;
          case "token":
            setWatching((w) => {
              if (!w || w.runId !== ev.runId) return w;
              const a = ev.audience as "officer" | "citizen";
              return { ...w, text: { ...w.text, [a]: w.text[a] + String(ev.text) }, attempt: { ...w.attempt, [a]: ev.attempt as number | string } };
            });
            break;
          case "agent.started":
            setWatching((w) => (w && w.runId === ev.runId ? { ...w, agent: { ...w.agent, started: true, model: String(ev.model), protocol: String(ev.protocol) } } : w));
            say(`The model starts investigating (${ev.protocol} tool calls, at most ${ev.maxCalls})`);
            break;
          case "agent.turn":
            if (ev.state !== "finished") break;
            setWatching((w) => (w && w.runId === ev.runId && ev.loop === "investigate"
              ? { ...w, agent: { ...w.agent, turns: [...w.agent.turns, ev as unknown as AgentTurn] } } : w));
            say(`Model turn ${ev.turn}: ${(ev.requested as { name: string }[]).map((r) => r.name).join(", ") || "no tool call"}`);
            break;
          case "agent.call": {
            const c = ev.call as AgentCall;
            let observation: unknown = ev.observation;
            try { observation = JSON.parse(String(ev.observation)); } catch { /* keep the text */ }
            setWatching((w) => (w && w.runId === ev.runId && ev.loop === "investigate"
              ? { ...w, agent: { ...w.agent, calls: [...w.agent.calls, { ...c, observation }] } } : w));
            say(`${c.id} ${c.name} ${c.ok ? `answered in ${c.ms} ms` : "refused"}`, c.ok ? undefined : "warn");
            break;
          }
          case "agent.finished":
            setWatching((w) => (w && w.runId === ev.runId ? { ...w, agent: { ...w.agent, stop: String(ev.stop), accepted: Number(ev.accepted), refused: Number(ev.refused) } } : w));
            say(`Investigation finished (${ev.stop}): ${ev.accepted} finding(s) grounded, ${ev.refused} refused`, Number(ev.refused) ? "warn" : "ok");
            break;
          case "reflect":
            say(`The model reviewed its rejected ${ev.audience} text and chose to ${ev.action === "redraft" ? "redraft" : "hand over to the template"}${ev.fix ? `: “${ev.fix}”` : ""}`);
            break;
          case "measure": {
            const s = ev.scores as { faithfulness: number };
            setWatching((w) =>
              w && w.runId === ev.runId
                ? { ...w, scores: [...w.scores, { audience: String(ev.audience), attempt: Number(ev.attempt), faithfulness: s.faithfulness, passed: Boolean(ev.passed), route: String(ev.route) }] }
                : w,
            );
            say(`Measured the ${ev.audience} text: faithfulness ${s.faithfulness.toFixed(2)} — ${ev.passed ? "accepted" : `rejected, ${ev.route}`}`, ev.passed ? "ok" : "warn");
            break;
          }
          case "run.finished":
            setWatching((w) => (w && w.runId === ev.runId ? { ...w, active: null, finished: String(ev.status), visited: w.visited.includes("finish") ? w.visited : [...w.visited, "finish"] } : w));
            say(`Finished ${ev.appId}: ${ev.status}${ev.verdict ? `, verdict ${ev.verdict}` : ""}`, ev.status === "anchored" ? "ok" : "bad");
            status.reload();
            break;
          case "block":
            say(`Block ${ev.number} committed (${ev.txCount} tx)`);
            break;
          default:
            break;
        }
      },
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token]);

  const st = status.data;
  return (
    <div className="stack-l">
      <dl className="stat-row" aria-label="Validator now">
        <div>
          <dt>Stream</dt>
          <dd style={{ fontSize: "var(--step-1)" }}>{conn === "open" ? "Live" : conn === "connecting" ? "Connecting" : "Reconnecting"}</dd>
        </div>
        <div>
          <dt>Queue</dt>
          <dd>{st?.queue.depth ?? "—"}</dd>
        </div>
        <div>
          <dt>Running</dt>
          <dd style={{ fontSize: "var(--step-1)" }}>{st?.queue.current?.appId ?? "idle"}</dd>
        </div>
        <div>
          <dt>Peer feed</dt>
          <dd style={{ fontSize: "var(--step-1)" }}>{st ? (st.listener.connected ? `block ${st.listener.lastBlock ?? "?"}` : "offline") : "—"}</dd>
        </div>
        <div>
          <dt>Model speed</dt>
          <dd style={{ fontSize: "var(--step-1)" }}>{st?.explainer.avgTokensPerSecond ? `${st.explainer.avgTokensPerSecond} tok/s` : "—"}</dd>
        </div>
      </dl>

      <section className="panel stack" aria-labelledby="h-live-graph">
        <div className="panel-head">
          <h2 id="h-live-graph">{watching ? <>Watching <span className="mono">{watching.appId}</span></> : "The graph"}</h2>
          <span className="small muted">{watching ? (watching.finished ? `finished: ${watching.finished}` : "running") : "waiting for the next run"}</span>
        </div>
        {graph.error && !graph.data ? (
          <ErrorState error={graph.error} onRetry={graph.reload} headingLevel={3} />
        ) : !graph.data ? (
          <>
            <SkeletonPanel height="10rem" />
            <Loading label="Loading the graph" />
          </>
        ) : (
          <GraphView graph={graph.data} active={watching?.active} visited={watching?.visited} failedNode={watching?.failed} />
        )}
        {!watching ? <p className="small muted">A run starts when a file reaches Revenue or the Collectorate, or when an officer presses “Validate again”. It will light up here as it goes.</p> : null}
      </section>

      {watching && watching.agent.started ? (
        <section className="panel stack" aria-labelledby="h-live-agent">
          <div className="panel-head">
            <h2 id="h-live-agent">The model’s investigation</h2>
            <span className="small muted">
              {watching.agent.stop
                ? `finished: ${watching.agent.stop} · ${watching.agent.accepted} grounded, ${watching.agent.refused} refused`
                : `${watching.agent.model} choosing its own tools`}
            </span>
          </div>
          <InvestigationTimeline turns={watching.agent.turns} calls={watching.agent.calls} running={!watching.agent.stop} />
        </section>
      ) : null}

      {watching && (watching.text.officer || watching.text.citizen || watching.scores.length) ? (
        <section className="panel stack" aria-labelledby="h-live-text" aria-live="off">
          <h2 id="h-live-text">What the model is writing</h2>
          <div className="grid-2">
            {(["officer", "citizen"] as const).map((a) => (
              <div key={a} className="stack-s stack">
                <h3 style={{ fontSize: "var(--step-0)" }}>
                  For {a === "officer" ? "officers" : "citizens"} <span className="small muted">attempt {String(watching.attempt[a] || "—")}</span>
                </h3>
                <blockquote className="explanation" data-audience={a}>
                  {watching.text[a] || <span className="faint">Not started</span>}
                </blockquote>
              </div>
            ))}
          </div>
          {watching.scores.length ? (
            <ol className="attempts">
              {watching.scores.map((s, i) => (
                <li key={i}>
                  <span className="title">
                    {s.audience} · attempt {s.attempt}: <Score value={s.faithfulness} label="Faithfulness" /> {s.passed ? "accepted" : `rejected → ${s.route}`}
                  </span>
                </li>
              ))}
            </ol>
          ) : null}
        </section>
      ) : null}

      <section className="panel" aria-labelledby="h-feed">
        <h2 id="h-feed" style={{ fontSize: "var(--step-1)", marginBottom: "var(--s-3)" }}>
          Event feed
        </h2>
        {lines.length === 0 ? (
          <p className="muted">Nothing yet. Events from the validator and the ledger appear here as they happen.</p>
        ) : (
          <ol className="feed" aria-live="polite">
            {lines.map((l) => (
              <li key={l.id} data-tone={l.tone}>
                <time className="small muted num" dateTime={new Date(l.at).toISOString()}>
                  {new Date(l.at).toLocaleTimeString("en-IN", { hour12: false, timeZone: "Asia/Kolkata" })}
                </time>
                <span>{l.text}</span>
              </li>
            ))}
          </ol>
        )}
      </section>
    </div>
  );
}
