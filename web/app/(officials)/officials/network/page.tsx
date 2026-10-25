"use client";

import { Hash } from "@/components/ui/hash";
import { ErrorState } from "@/components/ui/states";
import { Loading, SkeletonPanel } from "@/components/ui/skeleton";
import { TimeAgo } from "@/components/ui/time";
import { ORG_NAME } from "@/lib/format";
import type { MspId, NetworkNode, NetworkView } from "@/lib/types";
import { useApi } from "@/lib/useApi";

const orgName = (o: string | null) => (o && o in ORG_NAME ? ORG_NAME[o as MspId] : (o ?? ""));

function NodeCard({ n, leader }: { n: NetworkNode; leader: string | null }) {
  return (
    <article className="node-card" data-up={n.up} aria-label={n.name}>
      <header>
        <h3>{n.name}</h3>
        <span className="badge" data-tone={n.up ? "ok" : "bad"}>
          {n.up ? (n.kind === "orderer" ? (n.name === leader ? "Leader" : (n.state ?? "Up")) : "Up") : "Down"}
        </span>
      </header>
      <dl className="kv small">
        {n.org ? (
          <>
            <dt>Org</dt>
            <dd>{orgName(n.org)}</dd>
          </>
        ) : null}
        <dt>Port</dt>
        <dd className="mono">{n.port}</dd>
        {n.up && n.height != null ? (
          <>
            <dt>Height</dt>
            <dd>{n.height}</dd>
          </>
        ) : null}
        {n.up && n.kind === "orderer" ? (
          <>
            <dt>Raft term</dt>
            <dd>
              {n.term} · commit {n.commitIndex}/{n.lastLogIndex}
            </dd>
            <dt>Pending</dt>
            <dd>{n.pendingTx ?? 0}</dd>
          </>
        ) : null}
        {n.up && n.kind === "peer" ? (
          <>
            <dt>Chaincode</dt>
            <dd>
              {n.chaincode?.version} seq {n.chaincode?.sequence}
            </dd>
            <dt>State keys</dt>
            <dd>{n.worldStateKeys}</dd>
            <dt>Last commit</dt>
            <dd>{n.lastCommitAgoS == null ? "—" : `${Math.round(n.lastCommitAgoS)} s ago`}</dd>
          </>
        ) : null}
        {n.up && n.lastBlockHash ? (
          <>
            <dt>Tip</dt>
            <dd>
              <Hash value={n.lastBlockHash} head={8} tail={6} label="tip hash" />
            </dd>
          </>
        ) : null}
      </dl>
      {n.recentInvalid?.length ? (
        <p className="small" style={{ color: "var(--red)" }}>
          {n.recentInvalid.length} recently rejected, latest: {n.recentInvalid.at(-1)?.code}
        </p>
      ) : null}
    </article>
  );
}

export default function Network() {
  const res = useApi<NetworkView>("/api/network", undefined, { refreshMs: 4000 });
  const v = res.data;
  const groups: [string, NetworkNode["kind"]][] = [
    ["Orderers (Raft)", "orderer"],
    ["Peers", "peer"],
    ["Certificate authorities", "ca"],
    ["Services", "service"],
  ];
  return (
    <div className="stack-l">
      <header className="dash-head">
        <div>
          <h1>Nodes</h1>
          <p>The machines that keep the ledger. Three orderers agree on block order with Raft; each department runs its own peer.</p>
        </div>
        {v ? (
          <span className="small muted">
            Checked <TimeAgo value={v.checkedAt} />
          </span>
        ) : null}
      </header>
      {res.error && !v ? (
        <ErrorState error={res.error} onRetry={res.reload} />
      ) : !v ? (
        <>
          <SkeletonPanel height="12rem" />
          <Loading label="Checking the nodes" />
        </>
      ) : (
        <>
          <dl className="stat-row">
            <div>
              <dt>Channel</dt>
              <dd style={{ fontSize: "var(--step-1)" }}>{v.channel}</dd>
            </div>
            <div>
              <dt>Raft leader</dt>
              <dd style={{ fontSize: "var(--step-1)" }}>{v.leader ?? "none"}</dd>
            </div>
            <div>
              <dt>Block height</dt>
              <dd>{v.height ?? "—"}</dd>
            </div>
            <div>
              <dt>Nodes up</dt>
              <dd>
                {v.nodes.filter((n) => n.up).length}/{v.nodes.length}
              </dd>
            </div>
          </dl>
          {groups.map(([title, kind]) => {
            const ns = v.nodes.filter((n) => n.kind === kind);
            return ns.length ? (
              <section key={kind} aria-label={title}>
                <h2 style={{ fontSize: "var(--step-1)", marginBottom: "var(--s-3)" }}>{title}</h2>
                <div className="node-grid">
                  {ns.map((n) => (
                    <NodeCard key={n.name} n={n} leader={v.leader} />
                  ))}
                </div>
              </section>
            ) : null;
          })}
        </>
      )}
    </div>
  );
}
