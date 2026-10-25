"use client";

import { useState } from "react";

import { ChecksList, Score } from "@/components/app/parts";
import { VerdictBadge } from "@/components/ui/badges";
import { Hash } from "@/components/ui/hash";
import { IconDownload, Mark } from "@/components/ui/icons";
import { Timestamp } from "@/components/ui/time";
import { FindingsList, InvestigationTimeline } from "@/components/agent/investigation";
import { api, apiBytes, download, errorMessage } from "@/lib/api";
import { duration, VERDICT_INFO } from "@/lib/format";
import type { AgentRun, CheckResult, MeasureSummary, RunStatus, VerificationRecord, VerifierFinding, WriterMeta } from "@/lib/types";
import { useAction } from "@/lib/useApi";

const STATUS_TONE: Record<RunStatus, string> = {
  queued: "muted",
  running: "warn",
  signed: "warn",
  anchored: "ok",
  failed: "bad",
  skipped: "muted",
  quarantined: "bad",
};
const STATUS_WORD: Record<RunStatus, string> = {
  queued: "Queued",
  running: "Running",
  signed: "Signed",
  anchored: "Anchored",
  failed: "Failed",
  skipped: "Skipped",
  quarantined: "Quarantined",
};

export function RunStatusBadge({ status }: { status: RunStatus }) {
  return (
    <span className="badge" data-tone={STATUS_TONE[status] ?? "muted"}>
      {STATUS_WORD[status] ?? status}
    </span>
  );
}

export function WriterBadge({ w }: { w: Pick<WriterMeta, "engine" | "model"> }) {
  return (
    <span className="badge" data-tone={w.engine === "omlx" ? "col" : "muted"} title={w.model ?? "Rule-based template"}>
      {w.engine === "omlx" ? `Model: ${w.model ?? "oMLX"}` : "Template writer"}
    </span>
  );
}

// ---- measured explanations ----------------------------------------------------

const SCORE_ROWS: [keyof MeasureSummary["scores"], string, string][] = [
  ["entity", "Names, numbers and dates", "Every identifier, number, date and name appears in the checked facts"],
  ["sentenceSupport", "Sentence support", "Each sentence traces back to a check result"],
  ["consistency", "Consistency", "Verdict and counts match the rule-based verdict; decisions are attributed to the Collector"],
  ["coverage", "Coverage", "Every failed or warned check is mentioned"],
  ["faithfulness", "Faithfulness", "Weighted overall score; each hard violation halves it"],
];

function Explanation({ run, audience }: { run: AgentRun; audience: "officer" | "citizen" }) {
  const w = run.explanation?.[audience];
  const m: MeasureSummary | undefined = run.quality?.hallucination[audience];
  const gen = run.quality?.generation[audience];
  const judge = run.quality?.judge[audience];
  const labels = new Map((run.checks ?? []).map((c) => [c.id, c.label]));
  if (!w) return <p className="muted">No explanation was written for this run.</p>;
  return (
    <div className="stack">
      <div className="cluster">
        <WriterBadge w={w} />
        {w.faithfulness != null ? <Score value={w.faithfulness} label="Faithfulness" /> : null}
        {w.hallucinationRate != null ? (
          <span className="badge" data-tone={w.hallucinationRate === 0 ? "ok" : "warn"} title="Share of claims not supported by the facts">
            {(w.hallucinationRate * 100).toFixed(0)}% unsupported
          </span>
        ) : null}
        {(gen?.drafts ?? 0) > 1 ? <span className="badge" data-tone="warn">{gen?.drafts} drafts</span> : null}
        {w.fallbackReason ? <span className="badge" data-tone="warn">Fell back to template</span> : null}
        {gen?.tokensPerSecond ? <span className="small muted">{gen.tokensPerSecond} tok/s</span> : null}
      </div>

      <blockquote className="explanation" data-audience={audience}>
        {w.text}
      </blockquote>

      {w.fallbackReason ? (
        <p className="callout" data-tone="warn">
          <strong>Why the template was used:</strong> {w.fallbackReason}
        </p>
      ) : null}

      {m ? (
        <>
          <div className="table-wrap">
            <table className="table small">
              <caption className="visually-hidden">Hallucination measurement</caption>
              <thead>
                <tr>
                  <th scope="col">Measure</th>
                  <th scope="col" className="right">Score</th>
                  <th scope="col">What it means</th>
                </tr>
              </thead>
              <tbody>
                {SCORE_ROWS.map(([k, label, hint]) => (
                  <tr key={k}>
                    <th scope="row">{label}</th>
                    <td className="right">
                      <Score value={m.scores[k]} label={label} />
                    </td>
                    <td className="muted">{hint}</td>
                  </tr>
                ))}
                <tr>
                  <th scope="row">Claims checked</th>
                  <td className="right num">
                    {m.claims.supported}/{m.claims.total}
                  </td>
                  <td className="muted">Threshold {m.threshold}: {m.passed ? "passed" : "not passed"}</td>
                </tr>
              </tbody>
            </table>
          </div>

          {m.hardViolations.length ? (
            <div className="callout" data-tone="bad">
              <strong>Hard violations in the accepted text</strong>
              <ul>
                {m.hardViolations.map((v, i) => (
                  <li key={i}>{v}</li>
                ))}
              </ul>
            </div>
          ) : null}

          <details>
            <summary>Sentence by sentence ({m.sentences.length})</summary>
            <ol className="checklist" style={{ marginTop: "var(--s-3)" }}>
              {m.sentences.map((s, i) => (
                <li key={i}>
                  <Mark state={s.weak ? "warn" : "ok"} />
                  <div>
                    <div className="title">{s.text}</div>
                    <div className="detail">
                      support {s.score.toFixed(2)}
                      {s.supportedBy.length ? ` · from: ${s.supportedBy.map((id) => labels.get(id) ?? id).join("; ")}` : " · general wording"}
                    </div>
                  </div>
                </li>
              ))}
            </ol>
          </details>
        </>
      ) : null}

      {gen && gen.attempts.length ? (
        <details>
          <summary>Drafting attempts ({gen.attempts.length})</summary>
          <ol className="attempts">
            {gen.attempts.map((a) => (
              <li key={a.attempt + a.writer}>
                <Mark state={a.passed ? "ok" : "fail"} />
                <div>
                  <div className="title">
                    Attempt {a.attempt}: {a.writer === "omlx" ? "model" : "template"} <Score value={a.faithfulness} label="Faithfulness" />
                  </div>
                  {a.violations.length ? (
                    <ul className="evidence">
                      {a.violations.map((v, i) => (
                        <li key={i}>{v}</li>
                      ))}
                    </ul>
                  ) : (
                    <div className="detail">No violations</div>
                  )}
                </div>
              </li>
            ))}
          </ol>
          {(run.quality?.reflections?.[audience] ?? []).map((r, i) => (
            <p key={i} className="small">
              After attempt {r.attempt} {r.chosenBy === "model" ? "the model chose" : "the rule chose"} to{" "}
              <strong>{r.action === "redraft" ? "redraft" : "hand over to the template"}</strong>
              {r.fix ? `: “${r.fix}”` : r.note ? ` (${r.note})` : ""}.
            </p>
          ))}
          <p className="small muted">A rejected attempt goes back to the model with the checker’s complaint; the model chooses to redraft (at most once more) or to hand over to the template.</p>
        </details>
      ) : null}

      {judge ? (
        <p className="small muted">
          {judge.ran
            ? judge.usable
              ? `Second opinion from the model (advisory only): ${judge.agrees ? "agrees with the measurer" : "disagrees with the measurer on some sentences"}.`
              : `The model’s second opinion could not be used (${judge.reason ?? "unparseable"}).`
            : `Second opinion not requested${judge.reason ? `: ${judge.reason}` : ""}.`}
        </p>
      ) : null}
    </div>
  );
}

export function Explanations({ run }: { run: AgentRun }) {
  const [aud, setAud] = useState<"officer" | "citizen">("officer");
  return (
    <section className="panel stack" aria-labelledby="h-expl">
      <div className="panel-head">
        <h2 id="h-expl">Explanations, measured</h2>
        <div className="tabs" role="tablist" aria-label="Audience">
          {(["officer", "citizen"] as const).map((a) => (
            <button key={a} type="button" role="tab" id={`tab-${a}`} aria-selected={aud === a} aria-controls={`panel-${a}`} className="tab" onClick={() => setAud(a)}>
              {a === "officer" ? "For officers" : "For citizens"}
            </button>
          ))}
        </div>
      </div>
      <div role="tabpanel" id={`panel-${aud}`} aria-labelledby={`tab-${aud}`}>
        <Explanation run={run} audience={aud} />
      </div>
      {run.quality?.promptInjection.hits ? (
        <p className="callout" data-tone="warn">
          <strong>Prompt-injection text found:</strong> {run.quality.promptInjection.hits} instruction-like phrase(s) in the documents. They were removed before the model saw the facts, and the file fails the “no instructions aimed at an AI” check.
        </p>
      ) : null}
    </section>
  );
}

// ---- independent verifier -------------------------------------------------------------

function Findings({ findings }: { findings: (VerifierFinding & { ok?: boolean })[] }) {
  return (
    <ol className="checklist">
      {findings.map((f) => (
        <li key={f.id}>
          <Mark state={f.result === "pass" ? "ok" : f.result === "fail" ? "fail" : "skipped"} />
          <div>
            <div className="title">{f.label}</div>
            <div className="detail">{f.detail}</div>
          </div>
        </li>
      ))}
    </ol>
  );
}

export function VerificationPanel({ run, token, canRecheck }: { run: AgentRun; token: string; canRecheck: boolean }) {
  const [again, setAgain] = useState<VerificationRecord["pre"] | null>(null);
  const re = useAction(async () => api<{ passed: boolean; summary: string; findings: VerifierFinding[] }>(`/api/agent/runs/${run.runId}/verify`, { token, method: "POST" }));
  const v = run.verification;
  if (!v) return null;
  const shown = again ?? v.pre;
  return (
    <section className="panel stack" aria-labelledby="h-ver">
      <div className="panel-head">
        <h2 id="h-ver">Independent verifier</h2>
        <span className="badge" data-tone={shown.passed ? "ok" : "bad"}>
          {shown.passed ? "Passed" : "Failed"}
        </span>
      </div>
      <p className="muted">
        A second program, which shares no code path with the writer, re-derives the verdict, re-measures both explanations, and checks the hashes and both signatures before anything is anchored. {shown.summary}
      </p>
      <Findings findings={shown.findings} />
      {v.post ? (
        <>
          <h3 style={{ fontSize: "var(--step-0)" }}>After anchoring: read back from the ledger</h3>
          <Findings findings={v.post.findings} />
        </>
      ) : null}
      {canRecheck ? (
        <div className="cluster">
          <button type="button" className="btn" disabled={re.busy} onClick={() => void re.run().then((r) => r && setAgain({ passed: r.passed, summary: r.summary, findings: r.findings }))}>
            {re.busy ? <span className="spinner" aria-hidden="true" /> : null}
            Verify again now
          </button>
          {again ? <span className="small muted">Re-run just now over the stored files.</span> : null}
        </div>
      ) : null}
      {re.error ? (
        <p className="error-text" role="alert">
          {re.error}
        </p>
      ) : null}
    </section>
  );
}

// ---- files ---------------------------------------------------------------------------------

const FILES: [string, string][] = [
  ["report.json", "Report"],
  ["report.json.asc", "Report signature"],
  ["certificate.txt", "Certificate"],
  ["certificate.txt.asc", "Certificate signature"],
  ["trace.json", "Trace"],
];

export function FilesPanel({ run, token }: { run: AgentRun; token: string }) {
  const [problem, setProblem] = useState<string | null>(null);
  if (!run.report) return null;
  const r = run.report;
  const get = async (file: string) => {
    setProblem(null);
    try {
      download(file, await apiBytes(`/api/agent/runs/${run.runId}/${file}`, token), file.endsWith(".json") ? "application/json" : "text/plain");
    } catch (e) {
      setProblem(errorMessage(e));
    }
  };
  return (
    <section className="panel stack" aria-labelledby="h-files">
      <div className="panel-head">
        <h2 id="h-files">Signed files</h2>
        <span className="small muted">{r.schema}</span>
      </div>
      <dl className="kv">
        <dt>SHA-256</dt>
        <dd>
          <Hash value={r.sha256} full />
        </dd>
        <dt>Report CID</dt>
        <dd>
          <Hash value={r.cid} head={12} tail={8} label="report CID" />
        </dd>
        {r.certificateCid ? (
          <>
            <dt>Certificate CID</dt>
            <dd>
              <Hash value={r.certificateCid} head={12} tail={8} label="certificate CID" />
            </dd>
          </>
        ) : null}
        {run.anchor ? (
          <>
            <dt>Anchored</dt>
            <dd>
              block {run.anchor.block}, transaction <Hash value={run.anchor.txId} head={8} tail={4} label="transaction" />
            </dd>
          </>
        ) : (
          <>
            <dt>Anchored</dt>
            <dd>No: {run.status === "quarantined" ? "the report failed verification and was quarantined" : "not anchored"}</dd>
          </>
        )}
      </dl>
      <div className="cluster">
        {FILES.map(([file, label]) => (
          <button key={file} type="button" className="btn btn-small" onClick={() => void get(file)}>
            <IconDownload aria-hidden="true" /> {label}
          </button>
        ))}
      </div>
      {problem ? (
        <p className="error-text" role="alert">
          {problem}
        </p>
      ) : null}
      <p className="small muted">
        Anyone can check these offline: <code>python -m agent.verifier report.json report.json.asc key.asc</code> or plain <code>gpg --verify report.json.asc report.json</code>.
      </p>
    </section>
  );
}

// ---- trace ---------------------------------------------------------------------------------

export function TracePanel({ run }: { run: AgentRun }) {
  const [openSeq, setOpenSeq] = useState<number | null>(null);
  const max = Math.max(1, ...Object.values(run.timings ?? {}));
  return (
    <section className="panel stack" aria-labelledby="h-trace">
      <div className="panel-head">
        <h2 id="h-trace">What the agent did</h2>
        <span className="small muted">
          {run.steps.length} steps{run.resumed ? " · resumed after a restart" : ""}
        </span>
      </div>
      {run.timings && Object.keys(run.timings).length ? (
        <div className="timings" aria-label="Time spent in each stage">
          {Object.entries(run.timings).map(([node, ms]) => (
            <div key={node} className="timing">
              <span className="mono small">{node}</span>
              <span className="bar" aria-hidden="true">
                <span style={{ width: `${Math.max(2, (ms / max) * 100)}%` }} />
              </span>
              <span className="small muted num">{duration(ms)}</span>
            </div>
          ))}
        </div>
      ) : null}
      <ol className="steps">
        {run.steps.map((s) => (
          <li key={s.seq}>
            <button type="button" className="step-row" aria-expanded={openSeq === s.seq} onClick={() => setOpenSeq(openSeq === s.seq ? null : s.seq)}>
              <Mark state={s.status} />
              <span className="step-kind" data-kind={s.kind}>{s.kind}</span>
              <span className="step-label">{s.label}</span>
              <span className="small muted num">{duration(s.ms)}</span>
            </button>
            {openSeq === s.seq ? (
              <div className="step-body">
                {s.reason ? <p className="small">{s.reason}</p> : null}
                {s.input ? <pre className="codeblock">{JSON.stringify(s.input, null, 2)}</pre> : null}
                {s.output ? <pre className="codeblock">{JSON.stringify(s.output, null, 2)}</pre> : null}
              </div>
            ) : null}
          </li>
        ))}
      </ol>
    </section>
  );
}

// ---- the whole run -----------------------------------------------------------------------------

export function RunHeader({ run }: { run: AgentRun }) {
  const v = run.verdict;
  return (
    <section className="panel stack" aria-labelledby="h-run">
      <div className="panel-head">
        <h2 id="h-run">
          Run <span className="mono">{run.runId}</span>
        </h2>
        <div className="cluster">
          <RunStatusBadge status={run.status} />
          <VerdictBadge verdict={v?.status} flags={v?.flagCount} />
          {run.resumed ? <span className="badge" data-tone="warn">Resumed</span> : null}
        </div>
      </div>
      <dl className="kv">
        <dt>Application</dt>
        <dd className="mono">{run.appId}</dd>
        <dt>Started</dt>
        <dd>
          <Timestamp value={run.startedAt ?? run.createdAt} />
          {run.durationMs != null ? ` · ${duration(run.durationMs)}` : ""}
        </dd>
        <dt>Trigger</dt>
        <dd>
          {run.trigger.kind === "event" ? `Ledger event ${run.trigger.event ?? ""} in block ${run.trigger.block ?? "?"}` : run.trigger.kind === "manual" ? `Requested by ${run.trigger.requestedBy ?? "an officer"}` : run.trigger.kind}
        </dd>
        {v ? (
          <>
            <dt>Verdict</dt>
            <dd>
              <strong>{VERDICT_INFO[v.status].label}</strong>: {VERDICT_INFO[v.status].plain} {v.passed} passed, {v.attention} need attention, {v.failed} failed, {v.skipped} not run.
            </dd>
            <dt>Rule</dt>
            <dd className="small">{v.rule}</dd>
          </>
        ) : null}
      </dl>
      {run.error ? (
        <p className="callout" data-tone="bad">
          <strong>Stopped:</strong> {run.error}
        </p>
      ) : null}
      <p className="small muted">The verdict is computed by fixed rules from the check results. The language model chooses how to investigate and writes the explanations; its findings count only when grounded, and can raise a file to attention but never decide it.</p>
    </section>
  );
}

export function InvestigationPanel({ run }: { run: AgentRun }) {
  const inv = run.investigation;
  if (!inv) return null;
  return (
    <section className="panel stack" aria-labelledby="h-inv">
      <div className="panel-head">
        <h2 id="h-inv">The model’s investigation</h2>
        <span className="small muted">
          {inv.ran
            ? `${inv.calls.length} tool call(s), ${inv.turns.length} turn(s), ${inv.protocol} protocol · stopped: ${inv.stop}`
            : "did not run"}
        </span>
      </div>
      {!inv.ran ? (
        <p className="muted">{inv.reason}</p>
      ) : (
        <>
          <p className="small muted">
            After the fixed checks the model ({inv.model}) chose its own read-only tools, within a budget of{" "}
            {inv.budget?.maxCalls} calls. Every finding must cite the calls it rests on; a deterministic check refuses
            any finding whose numbers, names or identifiers are not in those results, and the independent verifier runs
            that check again. Grounded concerns can raise a file to “attention”, never decide it.
          </p>
          <h3 style={{ fontSize: "var(--step-0)" }}>Findings</h3>
          <FindingsList findings={inv.findings} refused={inv.refused} />
          {inv.summary ? <p className="small">Its summary: “{inv.summary}”</p> : null}
          <details>
            <summary>Every turn and tool call</summary>
            <InvestigationTimeline turns={inv.turns} calls={inv.calls} />
          </details>
          {inv.brief ? (
            <details>
              <summary>The brief it was given</summary>
              <pre className="call-obs" style={{ maxHeight: "none" }}>{inv.brief}</pre>
            </details>
          ) : null}
        </>
      )}
    </section>
  );
}

export function RunDetail({ run, token, canRecheck }: { run: AgentRun; token: string; canRecheck: boolean }) {
  const checks: CheckResult[] = run.checks ?? [];
  return (
    <div className="stack-l">
      <RunHeader run={run} />
      {checks.length ? (
        <section className="panel" aria-labelledby="h-checks">
          <div className="panel-head">
            <h2 id="h-checks">Checks</h2>
            <span className="small muted">{checks.filter((c) => c.id !== "agent_investigation").length} fixed checks + the model’s investigation</span>
          </div>
          <ChecksList checks={checks} />
        </section>
      ) : null}
      <InvestigationPanel run={run} />
      {run.explanation ? <Explanations run={run} /> : null}
      <VerificationPanel run={run} token={token} canRecheck={canRecheck} />
      <FilesPanel run={run} token={token} />
      {run.steps.length ? <TracePanel run={run} /> : null}
    </div>
  );
}
