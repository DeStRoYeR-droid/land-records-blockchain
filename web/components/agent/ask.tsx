"use client";

import { useRef, useState } from "react";

import { stream } from "@/lib/sse";

const EXAMPLES = [
  "Why does the validator never approve a transfer?",
  "What does the hallucination measurer check?",
  "What happens if the model is offline?",
  "How can a citizen verify a report?",
];

interface Done {
  engine: string;
  model: string | null;
  sections: string[];
  ungroundedFigures: string[];
  tools?: { id: string; name: string; args: Record<string, unknown>; ok: boolean }[];
}

export function AskTab({ token }: { token: string }) {
  const [question, setQuestion] = useState("");
  const [runId, setRunId] = useState("");
  const [answer, setAnswer] = useState("");
  const [done, setDone] = useState<Done | null>(null);
  const [tools, setTools] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const ctrl = useRef<AbortController | null>(null);

  const ask = async (q = question) => {
    if (!q.trim()) return;
    ctrl.current?.abort();
    const c = new AbortController();
    ctrl.current = c;
    setBusy(true);
    setError(null);
    setAnswer("");
    setDone(null);
    setTools([]);
    try {
      await stream("/api/agent/ask", {
        token,
        method: "POST",
        body: { question: q.trim(), runId: runId.trim() || undefined },
        signal: c.signal,
        onEvent: (ev) => {
          if (ev.type === "token") setAnswer((a) => a + String(ev.text));
          else if (ev.type === "tool") setTools((t) => [...t, `${(ev.call as { name: string }).name}(${Object.values((ev.call as { args: Record<string, unknown> }).args).join(", ")})`]);
          else if (ev.type === "done") setDone(ev as unknown as Done);
          else if (ev.type === "error") setError(String(ev.error));
        },
      });
    } catch (e) {
      if ((e as Error).name !== "AbortError") setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="stack-l">
      <form
        className="panel stack"
        onSubmit={(e) => {
          e.preventDefault();
          void ask();
        }}
      >
        <div className="field">
          <label htmlFor="question">Ask about LandChain or a run</label>
          <textarea id="question" className="textarea" rows={3} maxLength={600} value={question} onChange={(e) => setQuestion(e.target.value)} placeholder="For example: why was this file flagged?" />
        </div>
        <div className="field">
          <label htmlFor="ask-run">Run ID (optional)</label>
          <input id="ask-run" className="input input-mono" value={runId} onChange={(e) => setRunId(e.target.value)} placeholder="To ask about one run’s findings" spellCheck={false} />
        </div>
        <div className="cluster">
          <button className="btn btn-primary" type="submit" disabled={busy || !question.trim()}>
            {busy ? <span className="spinner" aria-hidden="true" /> : null}
            Ask
          </button>
          {EXAMPLES.map((x) => (
            <button key={x} type="button" className="btn btn-small btn-quiet" onClick={() => (setQuestion(x), void ask(x))}>
              {x}
            </button>
          ))}
        </div>
      </form>
      {error ? (
        <p className="error-text" role="alert">
          {error}
        </p>
      ) : null}
      {answer || busy ? (
        <section className="panel stack" aria-labelledby="h-answer" aria-live="polite">
          <h2 id="h-answer" style={{ fontSize: "var(--step-1)" }}>
            Answer
          </h2>
          <div className="answer">{answer || <span className="faint">{tools.length ? `Looking up: ${tools.join(", ")}…` : "Thinking…"}</span>}</div>
          {done ? (
            <div className="small muted">
              {done.engine === "omlx" ? `Written by ${done.model}` : done.engine === "retrieval-only" ? "The model is offline; these are the matching parts of the architecture notes" : "Not answered by the model"}
              {done.sections.length ? ` · from: ${done.sections.join("; ")}` : ""}
              {tools.length ? ` · looked up: ${tools.join(", ")}` : ""}
              {done.ungroundedFigures.length ? (
                <p className="callout" data-tone="warn" style={{ marginTop: "var(--s-2)" }}>
                  <strong>Check these figures:</strong> {done.ungroundedFigures.join(", ")} did not appear in anything the model looked up.
                </p>
              ) : done.engine === "omlx" ? (
                " · every number and identifier appears in the source notes"
              ) : null}
            </div>
          ) : null}
        </section>
      ) : null}
    </div>
  );
}
