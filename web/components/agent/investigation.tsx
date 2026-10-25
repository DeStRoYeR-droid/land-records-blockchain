"use client";

import type { AgentCall, AgentFinding, AgentTurn } from "@/lib/types";

function args(a: Record<string, unknown>): string {
  return Object.entries(a)
    .map(([k, v]) => `${k}=${typeof v === "string" ? `"${v}"` : JSON.stringify(v)}`)
    .join(", ");
}

function obs(o: unknown): string {
  const text = typeof o === "string" ? o : JSON.stringify(o, null, 1);
  return text.length > 900 ? `${text.slice(0, 900)}…` : text;
}

/**
 * The model's own investigation, turn by turn: what it said it would do, the tool it asked for,
 * what came back. Calls that were refused (unknown tool, bad arguments, a repeat) are shown in red.
 */
export function InvestigationTimeline({ turns, calls, running }: { turns: AgentTurn[]; calls: AgentCall[]; running?: boolean }) {
  const queue = [...calls];
  const items: ({ kind: "turn"; t: AgentTurn } | { kind: "call"; c: AgentCall })[] = [];
  for (const t of turns) {
    items.push({ kind: "turn", t });
    for (const r of t.requested.filter((x) => x.name !== "finish").slice(0, 2)) {
      const c = queue.shift();
      if (c) items.push({ kind: "call", c });
      void r;
    }
  }
  for (const c of queue) items.push({ kind: "call", c });
  if (!items.length) {
    return <p className="muted">{running ? "The model is reading its brief…" : "No turns recorded."}</p>;
  }
  return (
    <ol className="investigation-calls">
      {items.map((it, i) =>
        it.kind === "turn" ? (
          <li key={`t${i}`} data-kind="turn">
            <div className="call-head">
              <strong>Turn {it.t.turn}</strong>
              <span className="muted">
                {it.t.requested.length ? `asks for ${it.t.requested.map((r) => r.name).join(", ")}` : "no tool call"}
                {it.t.lastTurn ? " (budget used: finish only)" : ""}
              </span>
              <span className="faint small">{(it.t.ms / 1000).toFixed(1)} s</span>
            </div>
            {it.t.thought ? <p className="small" style={{ margin: 0 }}>“{it.t.thought}”</p> : null}
          </li>
        ) : (
          <li key={`c${i}`} data-kind="call" data-ok={it.c.ok}>
            <div className="call-head">
              <span className="badge" data-tone={it.c.ok ? "ok" : "bad"}>{it.c.id}</span>
              <span>
                {it.c.name}({args(it.c.args)})
              </span>
              <span className="faint small">{it.c.ok ? `${it.c.ms} ms` : "refused"}</span>
            </div>
            {it.c.observation !== undefined ? <pre className="call-obs">{obs(it.c.observation)}</pre> : null}
          </li>
        ),
      )}
      {running ? <li data-kind="turn"><span className="muted small">Thinking…</span></li> : null}
    </ol>
  );
}

export function FindingsList({ findings, refused }: { findings: AgentFinding[]; refused: AgentFinding[] }) {
  if (!findings.length && !refused.length) return <p className="muted">The model reported no findings.</p>;
  return (
    <ul className="findings">
      {findings.map((f, i) => (
        <li key={`a${i}`} data-severity={f.severity}>
          <strong>{f.severity === "concern" ? "Concern" : "Context"}:</strong> {f.claim}{" "}
          <span className="faint small mono">[{f.evidence.join(", ")}]</span>
        </li>
      ))}
      {refused.map((f, i) => (
        <li key={`r${i}`} data-refused="true">
          {f.claim} <span className="faint small mono">[{f.evidence.join(", ")}]</span>
          <span className="why small error-text">Refused: {(f.refusedBecause ?? []).join("; ")}</span>
        </li>
      ))}
    </ul>
  );
}
