"""
One validation run: start the LangGraph, follow it, keep the run record current.

The pipeline itself is in graph.py; this file only
  * opens the SQLite checkpointer (so interrupted runs resume),
  * feeds the graph an application id,
  * projects each state snapshot into the run record the dashboards read
    (steps, checks, verdict, explanations, report files, quality, timings),
  * publishes run.started / run.finished events.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Awaitable, Callable

import aiosqlite
import httpx
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from nodes.common import crypto, log
from nodes.sdk import Gateway

from .cidstore import CIDStore
from .explainer import Explainer
from .graph import ValidatorGraph
from .keys import AgentKey
from .store import RunStore

logger = log.get("investigator")
Publish = Callable[[dict[str, Any]], Awaitable[None]]


class Investigator:
    def __init__(self, key: AgentKey, gateway: Gateway, http: httpx.AsyncClient, explainer: Explainer,
                 store: RunStore, cids: CIDStore, publish: Publish):
        self.key, self.gw, self.http, self.explainer = key, gateway, http, explainer
        self.store, self.cids, self.publish = store, cids, publish
        self.vg: ValidatorGraph | None = None
        self._conn: aiosqlite.Connection | None = None

    async def open(self, checkpoint_db: Path) -> None:
        self._conn = await aiosqlite.connect(checkpoint_db)
        saver = AsyncSqliteSaver(self._conn)
        await saver.setup()
        self.vg = ValidatorGraph(self.key, self.gw, self.http, self.explainer, self.store, self.cids, self.publish,
                                 checkpointer=saver)

    async def close(self) -> None:
        if self._conn:
            await self._conn.close()

    # ------------------------------------------------------------------

    def project(self, run: dict[str, Any], s: dict[str, Any]) -> None:
        """Copy what the dashboards need from a graph state snapshot into the run record."""
        run["steps"] = s.get("traceSteps") or run.get("steps", [])
        run["timings"] = s.get("timings") or {}
        run["visited"] = s.get("visited") or []
        if s.get("app"):
            run["surveyNo"] = s["app"]["surveyNo"]
        if s.get("investigation"):
            run["investigation"] = s["investigation"]
        if s.get("verdict"):
            run["verdict"], run["checks"] = s["verdict"], s["checks"]
        drafts = s.get("drafts") or {}
        if drafts:
            measures = s.get("measures") or {}
            run["explanation"] = {
                a: {"text": d["text"], "engine": d["writer"], "model": d.get("model"),
                    "grounded": bool(measures.get(a, {}).get("passed")),
                    "fallbackReason": d.get("fallbackReason"), "drafts": d.get("drafts", 0),
                    "faithfulness": (measures.get(a) or {}).get("scores", {}).get("faithfulness"),
                    "hallucinationRate": (measures.get(a) or {}).get("scores", {}).get("hallucinationRate"),
                    "rejectedTokens": [v for att in d.get("attempts", []) if not att["passed"] for v in att["violations"]][:8],
                    "latencyMs": (d.get("gen") or {}).get("ms")}
                for a, d in drafts.items()}
        if s.get("report"):
            q = s["report"]["quality"]
            run["quality"] = {"aggregate": q["aggregate"], "hallucination": q["hallucination"],
                              "generation": q["generation"], "judge": q["judge"],
                              "promptInjection": q["promptInjection"], "reflections": q.get("reflections")}
        f = s.get("files")
        if f:
            run["report"] = {"reportId": s["report"]["reportId"], "sha256": f["reportSha256"], "cid": f["reportCid"],
                             "traceCid": f["traceCid"], "signatureCid": f.get("reportSignatureCid"),
                             "certificateCid": f["certificateCid"], "certificateSignatureCid": f.get("certificateSignatureCid"),
                             "bytes": f["bytes"], "schema": s["report"]["schema"]}
        if s.get("verification"):
            v = s["verification"]
            run["verification"] = {"pre": {k: v["pre"][k] for k in ("passed", "summary", "findings")},
                                   "post": v.get("post")}
        if s.get("anchor"):
            run["anchor"] = s["anchor"]

    @staticmethod
    def final_status(s: dict[str, Any]) -> str:
        if s.get("stop") == "skipped":
            return "skipped"
        if s.get("error"):
            return "failed"
        if s.get("outcome") == "quarantined":
            return "quarantined"
        return "anchored" if s.get("anchor") else "failed"

    async def run(self, run: dict[str, Any]) -> dict[str, Any]:
        assert self.vg, "open() first"
        run_id, app_id = run["runId"], run["appId"]
        config = {"configurable": {"thread_id": run_id}, "recursion_limit": 120}
        t_start = time.monotonic()
        snap = await self.vg.graph.aget_state(config)
        resuming = bool(snap.values and snap.next)
        run.update(status="running", startedAt=run.get("startedAt") or crypto.iso(crypto.utcnow()), resumed=resuming)
        self.store.save(run)
        await self.publish({"type": "run.started", "runId": run_id, "appId": app_id, "trigger": run["trigger"],
                            "resumed": resuming})
        last: dict[str, Any] = dict(snap.values) if resuming else {}
        try:
            start = None if resuming else {"runId": run_id, "appId": app_id, "trigger": run["trigger"]}
            if resuming:
                logger.info("resuming %s from node(s) %s", run_id, snap.next)
            async for state in self.vg.graph.astream(start, config, stream_mode="values"):
                last = state
                self.project(run, state)
                self.store.save(run)
            run["status"] = self.final_status(last)
            if last.get("error"):
                run["error"] = last["error"]
        except Exception as exc:
            logger.exception("run %s failed", run_id)
            run["status"], run["error"] = "failed", str(exc)[:500]
        finally:
            self.project(run, last)
            run["finishedAt"] = crypto.iso(crypto.utcnow())
            run["durationMs"] = int((time.monotonic() - t_start) * 1000) + (run.get("durationMs") or 0 if resuming else 0)
            self.store.save(run)
            self.vg.traces.pop(run_id, None)
            self.vg.tools.pop(run_id, None)
            await self.publish({"type": "run.finished", "runId": run_id, "appId": app_id, "status": run["status"],
                                "verdict": (run.get("verdict") or {}).get("status"), "durationMs": run["durationMs"],
                                "report": run.get("report"), "anchor": run.get("anchor"), "error": run.get("error"),
                                "quality": (run.get("quality") or {}).get("aggregate")})
        return run
