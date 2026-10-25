"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { Hash } from "@/components/ui/hash";
import { ErrorState } from "@/components/ui/states";
import { Loading, SkeletonTable } from "@/components/ui/skeleton";
import { Timestamp } from "@/components/ui/time";
import { api, ApiError } from "@/lib/api";
import { useDesk } from "@/lib/desk";
import { TX_CODE_LABEL } from "@/lib/format";
import { subscribe } from "@/lib/sse";
import type { BlockSummary, BlocksPage } from "@/lib/types";

const PAGE = 15;

export default function Ledger() {
  const { token } = useDesk();
  const [blocks, setBlocks] = useState<BlockSummary[]>([]);
  const [height, setHeight] = useState<number | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [loading, setLoading] = useState(true);
  const [more, setMore] = useState(false);
  const [live, setLive] = useState<"connecting" | "open" | "retrying">("connecting");
  const [open, setOpen] = useState<number | null>(null);
  const [fresh, setFresh] = useState<Set<number>>(new Set());
  const seen = useRef<number>(-1);

  const loadTop = useCallback(async () => {
    try {
      const page = await api<BlocksPage>(`/api/ledger/blocks?limit=${PAGE}`, { token });
      setHeight(page.height);
      setBlocks((old) => {
        const known = new Map(old.map((b) => [b.number, b]));
        for (const b of page.blocks) known.set(b.number, b);
        return [...known.values()].sort((a, b) => b.number - a.number);
      });
      setError(null);
    } catch (e) {
      setError(e instanceof ApiError ? e : new ApiError(0, String(e)));
    } finally {
      setLoading(false);
    }
  }, [token]);

  useEffect(() => {
    void loadTop();
  }, [loadTop]);

  useEffect(
    () =>
      subscribe("/api/events?backlog=0", {
        token,
        onStatus: setLive,
        onEvent: (ev) => {
          if (ev.type !== "block") return;
          const n = Number(ev.number);
          if (n <= seen.current) return;
          seen.current = n;
          setFresh((f) => new Set(f).add(n));
          setTimeout(() => setFresh((f) => (f.delete(n), new Set(f))), 4000);
          void loadTop();
        },
      }),
    [token, loadTop],
  );

  const older = async () => {
    const last = blocks.at(-1);
    if (!last || last.number === 0) return;
    setMore(true);
    try {
      const page = await api<BlocksPage>(`/api/ledger/blocks?limit=${PAGE}&before=${last.number - 1}`, { token });
      setBlocks((old) => [...old, ...page.blocks.filter((b) => !old.some((o) => o.number === b.number))]);
    } catch (e) {
      setError(e instanceof ApiError ? e : new ApiError(0, String(e)));
    } finally {
      setMore(false);
    }
  };

  return (
    <div className="stack-l">
      <header className="dash-head">
        <div>
          <h1>Ledger</h1>
          <p>Every block committed to the channel, newest first. Each block holds the hash of the one before it.</p>
        </div>
        <span className="badge" data-tone={live === "open" ? "ok" : "warn"} role="status">
          {live === "open" ? "Live" : live === "connecting" ? "Connecting…" : "Reconnecting…"}
          {height != null ? ` · height ${height}` : ""}
        </span>
      </header>
      {error && blocks.length === 0 ? (
        <ErrorState error={error} onRetry={() => void loadTop()} />
      ) : loading ? (
        <>
          <SkeletonTable rows={8} cols={5} />
          <Loading label="Loading blocks" />
        </>
      ) : (
        <div className="panel panel-flush">
          <ol className="blocklist">
            {blocks.map((b) => {
              const isOpen = open === b.number;
              return (
                <li key={b.number} data-fresh={fresh.has(b.number) || undefined}>
                  <button type="button" className="block-row" aria-expanded={isOpen} onClick={() => setOpen(isOpen ? null : b.number)}>
                    <span className="block-no">#{b.number}</span>
                    <span className="block-kind">{b.kind === "config" ? "Configuration" : b.kind === "genesis" ? "Genesis" : `${b.txCount} ${b.txCount === 1 ? "transaction" : "transactions"}`}</span>
                    <span className="small muted">
                      {b.validCount}/{b.txCount} valid
                    </span>
                    <span className="small muted">{b.orderer ?? ""}</span>
                    <span className="small muted">
                      <Timestamp value={b.timestamp} />
                    </span>
                  </button>
                  {isOpen ? (
                    <div className="block-body">
                      <dl className="kv">
                        <dt>Hash</dt>
                        <dd>
                          <Hash value={b.hash} head={16} tail={8} label="block hash" />
                        </dd>
                        <dt>Previous</dt>
                        <dd>
                          <Hash value={b.previousHash} head={16} tail={8} label="previous hash" />
                        </dd>
                        {b.raft ? (
                          <>
                            <dt>Raft</dt>
                            <dd>
                              term {b.raft.term}, index {b.raft.index}
                            </dd>
                          </>
                        ) : null}
                      </dl>
                      {b.txs.length ? (
                        <div className="table-wrap">
                          <table className="table">
                            <caption className="visually-hidden">Transactions in block {b.number}</caption>
                            <thead>
                              <tr>
                                <th scope="col">Function</th>
                                <th scope="col">By</th>
                                <th scope="col">Result</th>
                                <th scope="col">Transaction</th>
                              </tr>
                            </thead>
                            <tbody>
                              {b.txs.map((t) => (
                                <tr key={t.txId ?? t.index}>
                                  <td className="mono">{t.fn}</td>
                                  <td>
                                    {t.creator ? (
                                      <>
                                        {t.creator.name} <span className="small muted">({t.creator.role ?? t.creator.mspId})</span>
                                      </>
                                    ) : (
                                      "—"
                                    )}
                                  </td>
                                  <td>
                                    <span className="badge" data-tone={t.code === "VALID" ? "ok" : "bad"} title={t.detail}>
                                      {TX_CODE_LABEL[t.code ?? ""] ?? t.code}
                                    </span>
                                  </td>
                                  <td>
                                    <Hash value={t.txId} head={8} tail={4} label="transaction" />
                                  </td>
                                </tr>
                              ))}
                            </tbody>
                          </table>
                        </div>
                      ) : null}
                    </div>
                  ) : null}
                </li>
              );
            })}
          </ol>
          {blocks.at(-1) && blocks.at(-1)!.number > 0 ? (
            <div style={{ padding: "var(--s-4) var(--s-5)" }}>
              <button type="button" className="btn" onClick={() => void older()} disabled={more}>
                {more ? "Loading…" : "Older blocks"}
              </button>
            </div>
          ) : null}
        </div>
      )}
    </div>
  );
}
