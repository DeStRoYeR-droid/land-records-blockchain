"use client";

import type { GraphDescription } from "@/lib/types";

const STAGES: { title: string; note: string; nodes: string[] }[] = [
  { title: "1 · Understand the file", note: "Read the ledger, DigiLocker and the geospatial database; run 15 fixed checks.", nodes: ["plan", "gather", "run_checks"] },
  { title: "2 · Investigate", note: "The model chooses read-only tools, reads the results and reports findings; only grounded ones count. Then the rules set the verdict.", nodes: ["investigate", "rule_verdict"] },
  { title: "3 · Explain, then measure", note: "The model drafts, the measurer scores; on a rejection the model reviews the complaint and chooses to redraft or hand over to the template.", nodes: ["prepare", "draft", "measure", "reflect", "fallback", "accept", "judge"] },
  { title: "4 · Report", note: "Write report.json and certificate.txt, sign both with OpenPGP, and have the independent verifier re-check them.", nodes: ["compose", "sign", "verify"] },
  { title: "5 · Anchor", note: "Only a verified report is anchored on the ledger and read back. Otherwise it is quarantined.", nodes: ["anchor_report", "confirm", "quarantine", "finish"] },
];

const CONDITIONAL = new Set(["reflect", "fallback", "judge", "quarantine"]);

export type NodeState = "pending" | "active" | "done" | "error";

/**
 * The real compiled LangGraph, drawn by stage. `active` is the node running now;
 * `visited` the nodes that have run (in the run being watched).
 */
export function GraphView({
  graph,
  active,
  visited,
  failedNode,
}: {
  graph: GraphDescription;
  active?: string | null;
  visited?: string[];
  failedNode?: string | null;
}) {
  const label = new Map(graph.nodes.map((n) => [n.id, n.label]));
  const seen = new Set(visited ?? []);
  const stateOf = (id: string): NodeState => (id === failedNode ? "error" : id === active ? "active" : seen.has(id) ? "done" : "pending");
  return (
    <div className="graph" role="group" aria-label="The validator's LangGraph">
      {STAGES.map((st) => (
        <section key={st.title} className="graph-stage" aria-label={st.title}>
          <h3>{st.title}</h3>
          <p className="small muted">{st.note}</p>
          <ol>
            {st.nodes
              .filter((id) => label.has(id))
              .map((id) => {
                const s = stateOf(id);
                return (
                  <li key={id} className="gnode" data-state={s} data-conditional={CONDITIONAL.has(id) || undefined} title={label.get(id)}>
                    <span className="gname mono">{id}</span>
                    <span className="gdesc">{label.get(id)}</span>
                    <span className="visually-hidden">
                      {s === "active" ? " (running now)" : s === "done" ? " (done)" : s === "error" ? " (failed)" : ""}
                      {CONDITIONAL.has(id) ? " (only on some runs)" : ""}
                    </span>
                  </li>
                );
              })}
          </ol>
        </section>
      ))}
    </div>
  );
}
