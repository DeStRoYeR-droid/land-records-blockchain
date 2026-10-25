"""
The Agentic AI Validator service.

    listener   subscribes to peer0.org2's block events (Fabric's event service)
               and queues a run for every committed HandoffToRevenue event.
               Its position is checkpointed, so a restart resumes where it was.
    catch-up   on start, any file still WITH_REVENUE without a report gets a
               run (handoffs that happened while the agent was down)
    worker     runs one investigation at a time (a small local model can't do
               more in parallel anyway)

API (proxied by the gateway for the dashboards)
    GET  /status            identity, attested key, explainer, listener, queue, counters
    GET  /runs, /runs/{id}  history and full detail (every step)
    GET  /runs/{id}/report.json[.asc] | certificate.txt[.asc] | trace.json   the exact signed bytes
    POST /runs/{id}/verify  run the independent verifier again over the stored files
    GET  /graph, /metrics   the LangGraph (nodes, edges, Mermaid) and quality metrics over all runs
    GET  /stream            live Server-Sent Events: steps, tokens, run results
    POST /ask               ask about LandChain or a run (streamed answer)
    POST /runs              re-validate a file (needs X-Agent-Token)
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
import uuid
from typing import Any, AsyncIterator

import httpx
from fastapi import Body, Header
from fastapi.responses import PlainTextResponse, Response

from nodes.common import config, crypto, log
from nodes.common.msp import SigningIdentity
from nodes.common.web import NodeError, create_app, read_sse, sse_response
from nodes.sdk import Gateway

from . import keys, settings
from .cidstore import CIDStore
from . import metrics, qa, verifier
from .explainer import Explainer, knowledge_sections
from .investigator import Investigator
from .store import RunStore

logger = log.get("agent")
LISTEN_PEER = config.org_peer(settings.AGENT_MSP)


class AgentService:
    def __init__(self):
        self.key = keys.load_or_create(settings.AGENT_USER)
        self.identity = SigningIdentity.from_msp_dir(config.user_msp_dir(settings.AGENT_MSP, settings.AGENT_USER),
                                                     settings.AGENT_MSP)
        self.http = httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=3.0))
        self.gw = Gateway.for_identity(self.identity, http=self.http)
        self.store = RunStore(settings.DATA_DIR / "agent.db")
        self.cids = CIDStore(settings.DATA_DIR / "cid")
        self.explainer = Explainer()
        self.investigator = Investigator(self.key, self.gw, self.http, self.explainer, self.store, self.cids,
                                         self.publish)
        self.queue: asyncio.Queue = asyncio.Queue()
        self.queued: dict[str, dict[str, Any]] = {}
        self.current: dict[str, Any] | None = None
        self.subscribers: set[asyncio.Queue] = set()
        self.listener: dict[str, Any] = {"peer": LISTEN_PEER, "connected": False, "lastBlock": None,
                                         "lastEventAt": None, "handoffsSeen": 0}
        self.tasks: list[asyncio.Task] = []
        self.qa: Any = None
        self.started_at = crypto.iso(crypto.utcnow())
        self._onchain: tuple[float, Any] = (0.0, None)
        logger.info("validator %s ready: key %s, did %s...", settings.AGENT_USER, self.key.fingerprint,
                    self.key.did[:30])

    # -- pub/sub for the dashboard -----------------------------------------------

    async def publish(self, event: dict[str, Any]) -> None:
        event.setdefault("at", crypto.iso(crypto.utcnow()))
        for q in list(self.subscribers):
            with contextlib.suppress(asyncio.QueueFull):
                q.put_nowait(event)

    async def stream(self) -> AsyncIterator[dict[str, Any]]:
        q: asyncio.Queue = asyncio.Queue(maxsize=5000)
        self.subscribers.add(q)
        try:
            yield {"type": "hello", "status": await self.status()}
            while True:
                yield await q.get()
        finally:
            self.subscribers.discard(q)

    # -- lifecycle ------------------------------------------------------------------

    async def start(self) -> None:
        await self.investigator.open(settings.DATA_DIR / "checkpoints.db")
        self.qa = qa.build(self.explainer, self.gw, self.http)
        self.tasks = [asyncio.create_task(self._listen()), asyncio.create_task(self._work()),
                      asyncio.create_task(self._probe_loop()), asyncio.create_task(self._catch_up())]

    async def stop(self) -> None:
        for t in self.tasks:
            t.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        await self.investigator.close()
        await self.http.aclose()

    async def _probe_loop(self) -> None:
        while True:
            await self.explainer.probe(force=True)
            await asyncio.sleep(30)

    # -- queue -----------------------------------------------------------------------

    def enqueue(self, app_id: str, trigger: dict[str, Any]) -> dict[str, Any]:
        if app_id in self.queued:
            return self.queued[app_id]
        if self.current and self.current["appId"] == app_id and trigger.get("kind") != "manual":
            return self.current
        run = {"runId": f"run-{uuid.uuid4().hex[:8]}", "appId": app_id, "trigger": trigger, "status": "queued",
               "createdAt": crypto.iso(crypto.utcnow()), "createdTs": time.time(), "steps": []}
        self.store.save(run)
        self.queued[app_id] = run
        self.queue.put_nowait(run)
        logger.info("queued %s for %s (%s)", run["runId"], app_id, trigger.get("kind"))
        asyncio.get_running_loop().create_task(self.publish({"type": "run.queued", "runId": run["runId"],
                                                             "appId": app_id, "trigger": trigger}))
        return run

    async def _work(self) -> None:
        while True:
            run = await self.queue.get()
            self.queued.pop(run["appId"], None)
            self.current = run
            try:
                result = await self.investigator.run(run)
                logger.info("%s %s: %s (%s) in %d ms", run["runId"], run["appId"], result["status"],
                            (result.get("verdict") or {}).get("status"), result.get("durationMs", 0))
            except Exception:
                logger.exception("worker crashed on %s", run["runId"])
            finally:
                self.current = None

    # -- listening to the ledger -------------------------------------------------------

    async def _listen(self) -> None:
        url = config.url(LISTEN_PEER)
        backoff = 1.0
        while True:
            try:
                start = self.store.get_meta("last_block")
                if start is None:  # first ever start: don't replay history, catch-up handles open files
                    st = (await self.http.get(f"{url}/status", timeout=5)).json()
                    start_from = st["height"]
                else:
                    start_from = int(start) + 1
                async with self.http.stream("GET", f"{url}/events", params={"start": start_from},
                                            timeout=httpx.Timeout(None, connect=3.0)) as resp:
                    resp.raise_for_status()
                    self.listener["connected"] = True
                    logger.info("listening to %s block events from block %d", LISTEN_PEER, start_from)
                    await self.publish({"type": "listener", "connected": True, "fromBlock": start_from})
                    backoff = 1.0
                    async for _event, data in read_sse(resp):
                        await self._on_block(json.loads(data))
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if self.listener["connected"]:
                    logger.warning("lost the event stream from %s (%s); reconnecting", LISTEN_PEER, exc)
                    await self.publish({"type": "listener", "connected": False})
                self.listener["connected"] = False
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 10.0)

    async def _on_block(self, block: dict[str, Any]) -> None:
        handoffs = []
        for tx in block.get("txs", []):
            ev = tx.get("event") or {}
            if tx.get("code") == "VALID" and ev.get("name") == "HandoffToRevenue":
                payload = json.loads(ev["payload"])
                handoffs.append(payload["appId"])
                self.listener["handoffsSeen"] += 1
                self.enqueue(payload["appId"], {"kind": "event", "event": "HandoffToRevenue",
                                                "block": block["number"], "txId": tx["txId"]})
        self.listener["lastBlock"] = block["number"]
        self.listener["lastEventAt"] = crypto.iso(crypto.utcnow())
        self.store.set_meta("last_block", str(block["number"]))
        await self.publish({"type": "block", "number": block["number"], "txCount": len(block.get("txs", [])),
                            "fns": [t.get("fn") for t in block.get("txs", [])], "handoffs": handoffs})

    def resume(self, run: dict[str, Any]) -> None:
        """Queue a run that was interrupted (queued or running when the process stopped) under its own id."""
        if run["appId"] in self.queued:
            return
        self.queued[run["appId"]] = run
        self.queue.put_nowait(run)
        logger.info("re-queued interrupted run %s for %s", run["runId"], run["appId"])

    async def _catch_up(self) -> None:
        await asyncio.sleep(2)
        for run in self.store.unfinished():
            self.resume(run)
        for _ in range(30):
            try:
                apps = json.loads(await self.gw.evaluate("landrec", "ListApplications", "WITH_REVENUE"))
                break
            except Exception:
                await asyncio.sleep(2)
        else:
            return
        for app in apps:
            if not app.get("validations"):
                self.enqueue(app["appId"], {"kind": "catch-up", "reason": "handed over while the validator was offline"})

    # -- status ------------------------------------------------------------------------

    async def onchain_key(self) -> Any:
        ts, value = self._onchain
        if time.monotonic() - ts < 15 and value is not None and value.get("status") == "ACTIVE":
            return value  # only positive answers are cached
        try:
            value = json.loads(await self.gw.evaluate("landrec", "ReadAgentKey", settings.AGENT_USER))
            value = {k: value.get(k) for k in ("pgpFingerprint", "did", "status", "attestedBy", "attestedAt")}
            value["matchesLocalKey"] = value["pgpFingerprint"] == self.key.fingerprint
        except Exception as exc:
            value = {"status": "NOT_ATTESTED", "error": str(exc)[:200], "matchesLocalKey": False}
        self._onchain = (time.monotonic(), value)
        return value

    async def status(self) -> dict[str, Any]:
        return {
            "name": settings.AGENT_USER, "software": settings.SOFTWARE, "startedAt": self.started_at,
            "identity": {"name": settings.AGENT_USER, "mspId": settings.AGENT_MSP, "role": "agent",
                         "x509Id": self.identity.identity.id_string(), **self.key.describe()},
            "onChainKey": await self.onchain_key(),
            "explainer": self.explainer.status(),
            "framework": {"orchestration": "LangGraph", "llm": "LangChain (ChatOpenAI -> oMLX)",
                          "checkpointer": "SQLite (resumes interrupted runs)",
                          "nodes": len(self.investigator.vg.NODES) if self.investigator.vg else 0,
                          "judge": settings.JUDGE_MODE, "faithfulnessMin": settings.FAITHFULNESS_MIN,
                          "draftAttempts": settings.DRAFT_ATTEMPTS},
            "listener": self.listener,
            "queue": {"depth": self.queue.qsize(), "queued": [r["appId"] for r in self.queued.values()],
                      "current": {k: self.current.get(k) for k in ("runId", "appId", "startedAt")} if self.current else None},
            "counters": self.store.counters(),
            "placement": "Registration (Org1) -> Revenue (Org2) handoff, on the HandoffToRevenue event",
            "authority": "Evidence only. May call SubmitValidationReport; every decision function refuses its certificate.",
        }

    def files_of(self, run_id: str) -> dict[str, Any]:
        run = self.store.get(run_id)
        if not run:
            raise NodeError(404, f"no run {run_id}")
        if not run.get("report"):
            raise NodeError(409, f"run {run_id} has no signed report ({run['status']})")
        return run


def build_app():
    svc = AgentService()

    @contextlib.asynccontextmanager
    async def lifespan(_app):
        await svc.start()
        yield
        await svc.stop()

    app = create_app("LandChain · Agentic AI Validator",
                     "Validates files at the Registration -> Revenue handoff. Evidence only, signed with OpenPGP, "
                     "anchored on the ledger.", lifespan=lifespan)
    app.state.svc = svc

    @app.get("/health")
    async def health():
        return {"status": "ok", "agent": settings.AGENT_USER}

    @app.get("/status")
    async def status():
        return await svc.status()

    @app.get("/runs")
    async def runs(limit: int = 50, appId: str | None = None):
        out = []
        for r in svc.store.list(max(1, min(limit, 200)), appId):
            r = dict(r)
            r["stepCount"] = len(r.pop("steps", []))
            r.pop("checks", None)
            out.append(r)
        return {"runs": out}

    @app.get("/runs/{run_id}")
    async def run_detail(run_id: str):
        run = svc.store.get(run_id)
        if not run:
            raise NodeError(404, f"no run {run_id}")
        return run

    @app.get("/runs/{run_id}/report.json")
    async def report_json(run_id: str):
        run = svc.files_of(run_id)
        return Response(svc.cids.get(run["report"]["cid"]), media_type="application/json",
                        headers={"Content-Disposition": f'attachment; filename="{run["report"]["reportId"]}.json"'})

    @app.get("/runs/{run_id}/report.json.asc")
    async def report_sig(run_id: str):
        run = svc.files_of(run_id)
        return PlainTextResponse(svc.cids.get(run["report"]["signatureCid"]).decode(),
                                 headers={"Content-Disposition": f'attachment; filename="{run["report"]["reportId"]}.json.asc"'})

    @app.get("/runs/{run_id}/trace.json")
    async def trace_json(run_id: str):
        run = svc.files_of(run_id)
        return Response(svc.cids.get(run["report"]["traceCid"]), media_type="application/json")

    @app.get("/runs/{run_id}/certificate.txt")
    async def certificate_txt(run_id: str):
        run = svc.files_of(run_id)
        cid = run["report"].get("certificateCid")
        if not cid:
            raise NodeError(404, "this report predates certificates")
        return PlainTextResponse(svc.cids.get(cid).decode(),
                                 headers={"Content-Disposition": f'attachment; filename="{run["report"]["reportId"]}.certificate.txt"'})

    @app.get("/runs/{run_id}/certificate.txt.asc")
    async def certificate_sig(run_id: str):
        run = svc.files_of(run_id)
        cid = run["report"].get("certificateSignatureCid")
        if not cid:
            raise NodeError(404, "this report predates certificates")
        return PlainTextResponse(svc.cids.get(cid).decode(),
                                 headers={"Content-Disposition": f'attachment; filename="{run["report"]["reportId"]}.certificate.txt.asc"'})

    @app.post("/runs/{run_id}/verify")
    async def reverify(run_id: str):
        """Run the independent verifier again over the stored files, right now."""
        run = svc.files_of(run_id)
        r = run["report"]
        res = verifier.verify_report(
            svc.cids.get(r["cid"]), svc.cids.get(r["signatureCid"]).decode(), svc.key.public_armored,
            trace_bytes=svc.cids.get(r["traceCid"]),
            certificate_bytes=svc.cids.get(r["certificateCid"]) if r.get("certificateCid") else None,
            certificate_signature=svc.cids.get(r["certificateSignatureCid"]).decode() if r.get("certificateSignatureCid") else None,
            report_cid=r["cid"])
        return {**res, "checkedAt": crypto.iso(crypto.utcnow()), "runId": run_id}

    @app.get("/graph")
    async def graph():
        return svc.investigator.vg.describe()

    @app.get("/metrics")
    async def get_metrics():
        return metrics.compute(svc.store.list(200))

    @app.get("/reports/{sha256}")
    async def by_report(sha256: str):
        run = svc.store.by_report(sha256)
        if not run:
            raise NodeError(404, "no report with that SHA-256 was produced by this validator")
        return {k: run.get(k) for k in ("runId", "appId", "status", "verdict", "report", "anchor", "finishedAt")}

    @app.get("/cid/{cid}")
    async def by_cid(cid: str):
        try:
            data = svc.cids.get(cid)
        except KeyError as exc:
            raise NodeError(404, f"no object {cid}") from exc
        return Response(data, media_type="application/octet-stream")

    @app.get("/public-key.asc")
    async def public_key():
        return PlainTextResponse(svc.key.public_armored)

    @app.get("/knowledge")
    async def knowledge():
        return {"sections": [{"title": t, "text": b} for t, b in knowledge_sections().items()]}

    @app.get("/stream")
    async def stream():
        return sse_response(svc.stream())

    @app.post("/runs")
    async def manual_run(body: dict = Body(...), x_agent_token: str | None = Header(None)):
        if x_agent_token != settings.CONTROL_TOKEN:
            raise NodeError(401, "X-Agent-Token required")
        app_id = str(body.get("appId", "")).strip()
        if not app_id:
            raise NodeError(400, "appId is required")
        run = svc.enqueue(app_id, {"kind": "manual", "requestedBy": body.get("requestedBy", "officer")})
        return {k: run[k] for k in ("runId", "appId", "status")}

    @app.post("/ask")
    async def ask(body: dict = Body(...)):
        question = str(body.get("question", "")).strip()[:600]
        if not question:
            raise NodeError(400, "question is required")
        run_context = None
        if body.get("runId"):
            run = svc.store.get(str(body["runId"]))
            if run and run.get("checks"):
                run_context = json.dumps({"applicationId": run["appId"], "verdict": run.get("verdict"),
                                          "checks": [{k: c[k] for k in ("label", "status", "detail")} for c in run["checks"]]})

        async def gen() -> AsyncIterator[dict[str, Any]]:
            q: asyncio.Queue = asyncio.Queue()

            async def on_token(text: str) -> None:
                await q.put({"type": "token", "text": text})

            async def on_event(e: dict[str, Any]) -> None:
                if e.get("type") == "agent.call":
                    await q.put({"type": "tool", "call": e["call"], "observation": e["observation"][:400]})
                elif e.get("type") == "agent.turn" and e.get("state") == "started":
                    await q.put({"type": "thinking", "turn": e.get("turn")})

            async def run_ask():
                try:
                    out = await svc.qa.ainvoke({"question": question, "run_context": run_context},
                                               {"configurable": {"on_token": on_token, "on_event": on_event}})
                    await q.put({"type": "done", "engine": out["engine"], "model": out.get("model"),
                                 "sections": [t for t, _ in out.get("sections", [])],
                                 "ungroundedFigures": out.get("problems", []),
                                 "tools": [{k: c[k] for k in ("id", "name", "args", "ok")} for c in out.get("calls", [])],
                                 "stop": out.get("stop")})
                except Exception as exc:
                    await q.put({"type": "error", "error": str(exc)[:200]})

            task = asyncio.create_task(run_ask())
            try:
                while True:
                    item = await q.get()
                    yield item
                    if item["type"] in ("done", "error"):
                        break
            finally:
                task.cancel()

        return sse_response(gen())

    return app
