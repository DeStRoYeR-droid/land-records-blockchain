"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense } from "react";

import { AskTab } from "@/components/agent/ask";
import { DesignTab } from "@/components/agent/design";
import { LiveTab } from "@/components/agent/live";
import { MetricsTab } from "@/components/agent/metrics";
import { RunsTab } from "@/components/agent/runs";
import { SkeletonPanel } from "@/components/ui/skeleton";
import { useSession } from "@/lib/session";

const TABS = [
  { id: "live", label: "Live" },
  { id: "runs", label: "Runs" },
  { id: "quality", label: "Quality" },
  { id: "ask", label: "Ask" },
  { id: "design", label: "How it is built" },
] as const;
type TabId = (typeof TABS)[number]["id"];

function Inner() {
  const { ready, officer } = useSession();
  const params = useSearchParams();
  const router = useRouter();
  const run = params.get("run");
  const tab: TabId = run ? "runs" : ((TABS.find((t) => t.id === params.get("tab"))?.id ?? "live") as TabId);

  if (!ready) return <SkeletonPanel height="12rem" />;
  if (!officer) {
    return (
      <section className="empty" aria-labelledby="h-gate" role="status">
        <h2 id="h-gate">Sign in as an officer to see the validator at work</h2>
        <p>The live graph, every run with its evidence, quality metrics and the Q&amp;A are for department officers. Anyone can still verify a report and see the validator’s signing key on this page.</p>
        <div className="cluster">
          <Link className="btn btn-primary" href="/officials/sign-in?next=/agent">
            Officer sign-in
          </Link>
          <Link className="btn" href="/verify">
            Verify a report
          </Link>
        </div>
      </section>
    );
  }
  const token = officer.token;
  const canRecheck = officer.user.role === "verifier" || officer.user.role === "collector";
  const go = (id: TabId) => router.replace(id === "live" ? "/agent" : `/agent?tab=${id}`, { scroll: false });
  return (
    <div className="stack-l">
      <div className="tabs" role="tablist" aria-label="Validator views">
        {TABS.map((t) => (
          <button key={t.id} type="button" role="tab" id={`agent-tab-${t.id}`} className="tab" aria-selected={tab === t.id} aria-controls="agent-panel" onClick={() => go(t.id)}>
            {t.label}
          </button>
        ))}
      </div>
      <div role="tabpanel" id="agent-panel" aria-labelledby={`agent-tab-${tab}`}>
        {tab === "live" ? <LiveTab token={token} /> : null}
        {tab === "runs" ? <RunsTab token={token} canRecheck={canRecheck} selected={run} /> : null}
        {tab === "quality" ? <MetricsTab token={token} /> : null}
        {tab === "ask" ? <AskTab token={token} /> : null}
        {tab === "design" ? <DesignTab token={token} /> : null}
      </div>
    </div>
  );
}

export function AgentConsole() {
  return (
    <Suspense fallback={<SkeletonPanel height="12rem" />}>
      <Inner />
    </Suspense>
  );
}
