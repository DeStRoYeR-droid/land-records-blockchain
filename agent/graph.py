"""
The validator as a LangGraph.

    START
      |
    plan -> gather -> run_checks -> investigate -> rule_verdict -> prepare
                                        |                            |
                     (a subgraph: think <-> act, the model           |
                      choosing read-only tools; agentic.py)          |
                          +------------------------------------------+
                          v
                        draft  <-----------------+       for each audience (officer, citizen):
                          |                      |         draft     the model writes a text (LangChain -> oMLX)
                          v                      |         measure   the hallucination measurer scores it
                        measure --rejected--> reflect      reflect   the model reads the checker's complaint and
                          |                      |                   chooses: redraft (with its own fix) or hand over
                          |                      +--template--> fallback -> measure   (the template writer)
                          v accept
                        accept --next audience--> draft
                          | done
                          v
                        judge -> compose -> sign -> verify --ok--> anchor_report -> confirm -> finish -> END
                                                      |
                                                      +--failed--> quarantine -> finish

Every node returns a small update to a typed state; nothing is shared except
that state. The state is checkpointed to SQLite after every node, so a run
that was interrupted (the laptop slept, the process was killed) continues
from the last finished node instead of starting over.

What the model decides, and what it cannot
------------------------------------------
It decides how to investigate: in `investigate` it picks tools (ledger,
geospatial database, the filed documents), reads the results and chooses the
next call, within a budget, and reports findings citing those calls. It
decides, in `reflect`, whether a rejected draft is worth redrafting. It
writes the two explanations.

It cannot decide the outcome. The 15 fixed checks and the verdict rule are
code. Its findings are accepted only if a deterministic grounding check finds
every number, name and identifier in the results it cites; accepted concerns
become one more check that can only *warn* (raise a file to "attention"),
never fail it, clear it, approve or reject. Its texts go into the report
only after `measure` accepts them, and `verify` re-measures the texts and
re-grounds the findings before anything is anchored. Every tool it can
reach is read-only.
"""

from __future__ import annotations

import base64
import hashlib
import importlib.metadata
import json
import time
from functools import wraps
from typing import Any, Awaitable, Callable, TypedDict

import httpx
from langgraph.graph import END, START, StateGraph

from chaincode.landrec.landrec import REPORTABLE
from nodes.common import crypto, log
from nodes.common.canonical import canonical, sha256_hex
from nodes.sdk import Gateway

from . import facts as facts_mod, guard, investigation as inv_mod, judge as judge_mod, lc_tools, prompts, quality
from . import report as report_mod, settings, verifier
from .agentic import _json_objects
from .checks import CHECKS, Evidence, verdict_of
from .cidstore import CIDStore
from .explainer import Explainer, template_explanation
from .keys import AgentKey
from .pgp import verify_detached
from .store import RunStore
from .tools import Tools, Trace, sha256_text

logger = log.get("graph")
Publish = Callable[[dict[str, Any]], Awaitable[None]]
GRAPH_NAME = "landchain-validator"
GRAPH_VERSION = "3"
NOTICE = ("This report is evidence for the Revenue verifier and the Collector. It does not approve or reject "
          "anything; that decision belongs to the Collector.")
AUDIENCES = ("officer", "citizen")


class RunState(TypedDict, total=False):
    runId: str
    appId: str
    trigger: dict[str, Any]
    # evidence
    app: dict[str, Any]
    parcel: dict[str, Any]
    history: list[dict[str, Any]]
    apps: list[dict[str, Any]]
    parcels: list[dict[str, Any]]
    citizens: dict[str, Any]
    holders: dict[str, Any]
    docs: dict[str, Any]
    computed: dict[str, str]
    purpose: str
    # rules
    checks: list[dict[str, Any]]
    investigation: dict[str, Any]
    verdict: dict[str, Any]
    facts: dict[str, Any]
    promptChanged: bool
    # writing
    cursor: int
    attempt: int
    feedback: str | None
    current: dict[str, Any]
    history_of: dict[str, list[dict[str, Any]]]
    drafts: dict[str, Any]
    measures: dict[str, Any]
    judges: dict[str, Any]
    reflections: dict[str, list[dict[str, Any]]]
    route: str
    # artefacts
    report: dict[str, Any]
    files: dict[str, Any]
    verification: dict[str, Any]
    anchor: dict[str, Any]
    # bookkeeping
    traceSteps: list[dict[str, Any]]
    timings: dict[str, int]
    visited: list[str]
    stop: str | None
    error: str | None
    outcome: str


def library_versions() -> dict[str, str]:
    out = {}
    for name in ("langgraph", "langchain-core", "langchain-openai"):
        try:
            out[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            out[name] = "unknown"
    return out


class ValidatorGraph:
    """Owns the nodes. One instance serves every run; per-run objects are keyed by run id."""

    NODES = ["plan", "gather", "run_checks", "investigate", "rule_verdict", "prepare", "draft", "measure", "reflect",
             "fallback", "accept", "judge", "compose", "sign", "verify", "anchor_report", "confirm", "quarantine", "finish"]
    DESCRIPTIONS = {
        "plan": "State the policy for this run",
        "gather": "Read the file from the ledger and DigiLocker with LangChain tools",
        "run_checks": "Run the 15 fixed checks, following up where something looks wrong",
        "investigate": "The model chooses read-only tools to look into the open leads, then reports grounded findings",
        "rule_verdict": "Compute the verdict by rule (never the model)",
        "prepare": "Build the facts the model may mention and sanitise them",
        "draft": "The model writes one explanation (officer, then citizen)",
        "measure": "Score the text for hallucination against the facts",
        "reflect": "The model reads why its text was rejected and chooses: redraft with a fix, or hand over",
        "fallback": "Replace a text that kept failing with the template writer",
        "accept": "Keep the accepted text and move to the next audience",
        "judge": "Optional second opinion from the model (advisory)",
        "compose": "Assemble report.json and certificate.txt, store them by CID",
        "sign": "OpenPGP-sign both files and check the signatures",
        "verify": "Independent verifier re-checks the signed files",
        "anchor_report": "Anchor the report's hash on the ledger",
        "confirm": "Read the anchor back from the ledger",
        "quarantine": "A report that fails verification is kept but never anchored",
        "finish": "Close the run",
    }

    def __init__(self, key: AgentKey, gateway: Gateway, http: httpx.AsyncClient, model: Explainer, store: RunStore,
                 cids: CIDStore, publish: Publish, checkpointer: Any = None):
        self.key, self.gw, self.http, self.model = key, gateway, http, model
        self.store, self.cids, self.publish = store, cids, publish
        self.traces: dict[str, Trace] = {}
        self.tools: dict[str, Tools] = {}
        self.graph = self._build().compile(checkpointer=checkpointer)

    # ------------------------------------------------------------------ plumbing

    def _trace(self, state: RunState) -> Trace:
        run_id = state["runId"]
        if run_id not in self.traces:
            self.traces[run_id] = Trace.restore(run_id, self.publish, state.get("traceSteps") or [])
        return self.traces[run_id]

    def _tools(self, state: RunState) -> Tools:
        run_id = state["runId"]
        if run_id not in self.tools:
            self.tools[run_id] = Tools(self.gw, self.http, self._trace(state))
        return self.tools[run_id]

    def _node(self, name: str):
        """Wrap a node: announce it, time it, save the trace into the state, and turn an exception into state."""
        def deco(fn):
            @wraps(fn)
            async def wrapper(state: RunState) -> dict[str, Any]:
                if state.get("stop") or state.get("error"):
                    return {}
                run_id = state["runId"]
                await self.publish({"type": "node", "runId": run_id, "node": name, "state": "started"})
                t0 = time.monotonic()
                try:
                    update = await fn(state) or {}
                except Exception as exc:
                    logger.exception("node %s failed in %s", name, run_id)
                    await self._trace(state).note("decision", "error", f"Step '{name}' failed", str(exc)[:400], status="error")
                    update = {"error": f"{name}: {str(exc)[:400]}"}
                ms = int((time.monotonic() - t0) * 1000)
                timings = dict(state.get("timings") or {})
                timings[name] = timings.get(name, 0) + ms
                update = {**update, "timings": timings, "visited": (state.get("visited") or []) + [name],
                          "traceSteps": self._trace(state).as_list()}
                await self.publish({"type": "node", "runId": run_id, "node": name, "state": "finished", "ms": ms,
                                    "error": update.get("error")})
                return update
            return wrapper
        return deco

    # ------------------------------------------------------------------ nodes

    async def plan(self, state: RunState) -> dict[str, Any]:
        await self._trace(state).note(
            "decision", "plan", "Plan the investigation",
            "Read the file and the network-wide picture from the ledger, pull every filed document from DigiLocker "
            f"and hash it locally, run the {len(CHECKS)} fixed checks, then let the model investigate the open leads "
            f"with read-only tools (at most {settings.AGENT_MAX_CALLS} calls), ground its findings, explain the "
            "results, measure what it wrote, sign, verify independently and only then anchor. Evidence only: no decisions.",
            output={"graph": GRAPH_NAME, "nodes": self.NODES, "agentBudget": {"maxCalls": settings.AGENT_MAX_CALLS,
                                                                                "maxTurns": settings.AGENT_MAX_TURNS}})
        return {}

    async def gather(self, state: RunState) -> dict[str, Any]:
        trace, tools = self._trace(state), self.tools_for(state)
        app_id = state["appId"]
        app = await tools["ledger_read_application"].ainvoke(
            {"app_id": app_id, "reason": "Start from the file Registration handed over"})
        if app["status"] not in REPORTABLE:
            await trace.note("decision", "skip", f"Application is {app['status']}: nothing to validate",
                             "The validator only reports while a file is with Revenue or the Collectorate.")
            return {"stop": "skipped", "app": app}
        survey = app["surveyNo"]
        parcel = await tools["ledger_read_parcel"].ainvoke({"survey_no": survey, "reason": "The ledger's record of the land being sold"})
        history = await tools["ledger_parcel_history"].ainvoke({"survey_no": survey, "reason": "Earlier owners and transfers, for the re-sale check"})
        apps = await tools["ledger_list_applications"].ainvoke({"reason": "Cross-file view: earlier flags and comparable prices"})
        parcels = await tools["ledger_list_parcels"].ainvoke({"reason": "Areas and districts of comparable transfers"})
        citizens, holders = {}, {}
        purpose = f"Validation of {app_id} at the Registration-to-Revenue handoff"
        for party in ("seller", "buyer"):
            citizens[party] = await tools["ledger_read_citizen"].ainvoke(
                {"owner_ref": app[f"{party}Ref"], "party": party, "reason": f"Which DID Registration bound to the {party}"})
            holders[party] = await tools["digilocker_resolve_holder"].ainvoke(
                {"owner_ref": app[f"{party}Ref"], "party": party, "purpose": purpose,
                 "reason": f"The {party}'s Aadhaar-verified name, to compare with the deed"})
        docs, computed = {}, {}
        for d in app["documents"]:
            try:
                f = await tools["digilocker_fetch_document"].ainvoke(
                    {"doc_id": d["docId"], "purpose": purpose, "label": f"Pull {d['documentType']} {d['docId']} from DigiLocker",
                     "reason": f"Pull the filed {d['documentType']} to hash it myself and read it"})
            except Exception as exc:  # recorded in the trace; the integrity check reports it
                logger.warning("could not fetch %s: %s", d["docId"], exc)
                continue
            docs[d["docId"]] = f
            computed[d["docId"]] = sha256_text(f["content"])
        return {"app": app, "parcel": parcel, "history": history, "apps": apps, "parcels": parcels,
                "citizens": citizens, "holders": holders, "docs": docs, "computed": computed, "purpose": purpose}

    def tools_for(self, state: RunState) -> dict[str, Any]:
        return lc_tools.build(self._tools(state))

    async def run_checks(self, state: RunState) -> dict[str, Any]:
        trace, tools = self._trace(state), self._tools(state)
        ev = Evidence(state["app"], state["parcel"], state["history"], state["apps"], state["parcels"], state["docs"],
                      state["computed"], state["holders"], state["citizens"], state["purpose"])
        checks = []
        for check in CHECKS:
            t0 = time.monotonic()
            r = await check(ev, tools, trace)
            checks.append(r)
            await trace.note("check", r["id"], r["label"], r["detail"], status=r["status"],
                             output={"evidence": r["evidence"]}, ms=int((time.monotonic() - t0) * 1000))
        return {"checks": checks}

    async def investigate(self, state: RunState) -> dict[str, Any]:
        """The model's own investigation of the leads the fixed checks leave open (investigation.py)."""
        trace, tools, run_id = self._trace(state), self._tools(state), state["runId"]
        checks = list(state["checks"])
        ev = Evidence(state["app"], state["parcel"], state["history"], state["apps"], state["parcels"], state["docs"],
                      state["computed"], state["holders"], state["citizens"], state["purpose"])
        await self.model.probe()
        loop, facts, brief, note = None, {}, None, None
        if settings.INVESTIGATE == "off":
            note = "The investigation is switched off (LANDCHAIN_INVESTIGATE=off)."
        elif self.model.engine != "omlx":
            note = f"No language model is available ({self.model.last_error or 'template mode'}), so no investigation ran."
        else:
            async def emit(e: dict[str, Any]) -> None:
                await self.publish({**e, "runId": run_id})
            await self.publish({"type": "agent.started", "runId": run_id, "loop": "investigate", "model": self.model.model,
                                "protocol": self.model.protocol, "maxCalls": settings.AGENT_MAX_CALLS})
            try:
                loop, facts, brief = await inv_mod.investigate(self.model, ev, tools, checks, emit)
            except Exception as exc:
                logger.exception("investigation failed in %s", run_id)
                note = f"The investigation failed: {str(exc)[:160]}"
        chk, section = inv_mod.check_from(loop, facts, note)
        if loop:
            section["brief"] = brief
            await trace.note("llm", "investigate.brief", "The model's brief: the file, the fixed results, the open leads",
                             "Built by code from the evidence; the model chooses what to do with it.", status="info",
                             output={"brief": brief, "protocol": loop.protocol, "model": loop.model})
            queue = list(loop.calls)
            for t in loop.turns:
                asked = [r["name"] for r in t["requested"]]
                await trace.note("llm", f"investigate.turn{t['turn']}",
                                 f"Turn {t['turn']}: the model " + (f"asks for {', '.join(asked)}" if asked else "replies"),
                                 t["thought"] or "(no visible reasoning)", status="info", output={"requested": t["requested"]},
                                 ms=t["ms"])
                for _ in [r for r in asked if r != "finish"][:2]:
                    if queue:
                        c = queue.pop(0)
                        args = ", ".join(f"{k}={v!r}" for k, v in c["args"].items())
                        await trace.note("tool", f"agent.{c['name']}", f"{c['id']}: {c['name']}({args})",
                                         "Chosen by the model", status="ok" if c["ok"] else "error",
                                         output=c.get("observation"), ms=c["ms"])
            for f in section["refused"]:
                await trace.note("decision", "investigate.refused", f"Finding refused: {f['claim'][:90]}",
                                 "; ".join(f["refusedBecause"]), status="warn", output=f)
            await self.publish({"type": "agent.finished", "runId": run_id, "loop": "investigate", "stop": loop.stop,
                                "accepted": len(section["findings"]), "refused": len(section["refused"])})
        await trace.note("check", chk["id"], chk["label"], chk["detail"], status=chk["status"],
                         output={"evidence": chk["evidence"]})
        return {"checks": checks + [chk], "investigation": section}

    async def rule_verdict(self, state: RunState) -> dict[str, Any]:
        v = verdict_of(state["checks"])
        await self._trace(state).note(
            "decision", "verdict", f"Verdict: {v['status']} ({v['failed']} failed, {v['attention']} attention, "
            f"{v['passed']} passed)", f"Fixed rule, not the model: {v['rule']}.",
            status={"clear": "ok", "attention": "warn", "flagged": "fail"}[v["status"]], output=v)
        return {"verdict": v}

    async def prepare(self, state: RunState) -> dict[str, Any]:
        facts = facts_mod.build_facts(state["app"], state["parcel"], state["checks"], state["verdict"])
        await self.model.probe()
        changed = guard.sanitise(facts) != facts
        await self._trace(state).note(
            "decision", "prepare", "Facts for the writer",
            "Only these facts may appear in the explanations. Strings from documents are sanitised before they reach "
            "the model" + (" (some instruction-like text was removed)." if changed else "."),
            status="warn" if changed else "ok",
            output={"factsSha256": facts_mod.facts_sha256(facts), "engine": self.model.engine, "model": self.model.model,
                    "judge": settings.JUDGE_MODE})
        return {"facts": facts, "promptChanged": changed, "cursor": 0, "attempt": 0, "feedback": None,
                "history_of": {a: [] for a in AUDIENCES}, "drafts": {}, "measures": {}, "judges": {}, "reflections": {}}

    # -- writing: draft -> measure -> (retry | fallback | accept) ------------------------

    async def draft(self, state: RunState) -> dict[str, Any]:
        aud = AUDIENCES[state["cursor"]]
        attempt = state.get("attempt", 0) + 1
        facts, run_id = state["facts"], state["runId"]
        await self.model.probe()
        writer, reason, gen = "template", None, None
        if self.model.engine == "omlx":
            await self.publish({"type": "explain.started", "runId": run_id, "audience": aud, "attempt": attempt,
                                "engine": "omlx", "model": self.model.model})

            async def on_token(text: str) -> None:
                await self.publish({"type": "token", "runId": run_id, "audience": aud, "text": text, "attempt": attempt})
            try:
                gen = await self.model.draft(facts, aud, on_token, state.get("feedback"))
                text, writer = gen.text, "omlx"
            except Exception as exc:
                self.model.reachable, self.model.last_error = False, str(exc)
                logger.warning("oMLX draft failed (%s); using the template writer", exc)
                text, reason = template_explanation(facts, aud), f"oMLX error: {str(exc)[:160]}"
        else:
            text = template_explanation(facts, aud)
            reason = "template mode" if self.model.mode == "template" else f"oMLX unavailable: {self.model.last_error}"
            await self.publish({"type": "explain.started", "runId": run_id, "audience": aud, "attempt": attempt,
                                "engine": "template", "model": None})
            await self.publish({"type": "token", "runId": run_id, "audience": aud, "text": text, "attempt": attempt})
        current = {"audience": aud, "attempt": attempt, "text": text, "writer": writer, "model": self.model.model
                   if writer == "omlx" else None, "fallbackReason": reason, "gen": gen.meta() if gen else None}
        return {"attempt": attempt, "current": current}

    async def measure(self, state: RunState) -> dict[str, Any]:
        cur, facts = state["current"], state["facts"]
        aud = cur["audience"]
        m = quality.measure(cur["text"], facts, aud, settings.FAITHFULNESS_MIN)
        hist = {**state["history_of"]}
        hist[aud] = hist[aud] + [{"attempt": cur["attempt"], "writer": cur["writer"], "passed": m["passed"],
                                  "faithfulness": m["scores"]["faithfulness"], "hallucinationRate": m["scores"]["hallucinationRate"],
                                  "violations": m["hardViolations"][:4], "text": cur["text"][:600],
                                  "gen": cur.get("gen")}]
        if m["passed"] or cur["writer"] == "template":
            route = "accept"
        elif cur["attempt"] < settings.DRAFT_ATTEMPTS:
            route = "retry"
        else:
            route = "fallback"
        s = m["scores"]
        await self._trace(state).note(
            "measure", f"measure.{aud}", f"Hallucination check of the {aud} text (attempt {cur['attempt']}, {cur['writer']})",
            ("Scored against the facts without a model: invented numbers, identifiers, dates and names; unattributed "
             "decisions; a wrong verdict or count; left-out failures. " +
             ("Passed." if m["passed"] else "Rejected: " + "; ".join(m["hardViolations"][:3]) +
              (f". Next: {'the model reviews the complaint' if route == 'retry' else 'use the template writer'}." if cur["writer"] != "template" else "."))),
            status="ok" if m["passed"] else "fail",
            output={"faithfulness": s["faithfulness"], "hallucinationRate": s["hallucinationRate"], "scores": s,
                    "violations": m["hardViolations"][:6], "route": route, "writer": cur["writer"]})
        await self.publish({"type": "measure", "runId": state["runId"], "audience": aud, "attempt": cur["attempt"],
                            "passed": m["passed"], "scores": s, "route": route})
        return {"measures": {**state.get("measures", {}), aud: m}, "history_of": hist, "route": route,
                "feedback": m["feedback"] if route == "retry" else None}

    async def reflect(self, state: RunState) -> dict[str, Any]:
        """The model reads why its text was rejected and chooses the next step. Bounded: the number of drafts is still
        capped by LANDCHAIN_DRAFT_ATTEMPTS, and an unreadable answer means 'redraft'."""
        cur, aud = state["current"], state["current"]["audience"]
        feedback = state.get("feedback") or ""
        past = dict(state.get("reflections") or {})
        entry: dict[str, Any] = {"attempt": cur["attempt"], "action": "redraft", "fix": "", "chosenBy": "rule"}
        if settings.REFLECT != "off" and self.model.engine == "omlx":
            t0 = time.monotonic()
            try:
                gen = await self.model.chat(prompts.REFLECT.format_messages(
                    facts=prompts.render_facts(guard.sanitise(state["facts"])), text=cur["text"][:900],
                    feedback=feedback[:400]), 120, None, temperature=0.0)
                obj = next(iter(_json_objects(gen.text)), {})
                if obj.get("action") in ("redraft", "template"):
                    entry.update(action=obj["action"], fix=guard.sanitise_text(str(obj.get("fix") or ""), 200),
                                 chosenBy="model", ms=int((time.monotonic() - t0) * 1000))
                else:
                    entry.update(note="the model's answer was not a usable choice", raw=gen.text[:200])
            except Exception as exc:
                entry.update(note=f"model error: {str(exc)[:120]}")
        await self._trace(state).note(
            "llm" if entry["chosenBy"] == "model" else "decision", f"reflect.{aud}",
            f"After the rejected {aud} text: {'redraft' if entry['action'] == 'redraft' else 'hand over to the template'}"
            f" ({'the model chose' if entry['chosenBy'] == 'model' else 'by rule'})",
            (f"Its plan: {entry['fix']}" if entry.get("fix") else entry.get("note") or "No plan given."),
            status="info", output=entry, ms=entry.get("ms", 0))
        await self.publish({"type": "reflect", "runId": state["runId"], "audience": aud, **entry})
        past[aud] = (past.get(aud) or []) + [entry]
        plan = f" Your own plan for the fix: {entry['fix']}" if entry["action"] == "redraft" and entry.get("fix") else ""
        return {"route": "draft" if entry["action"] == "redraft" else "fallback", "feedback": feedback + plan,
                "reflections": past}

    async def fallback(self, state: RunState) -> dict[str, Any]:
        cur, aud = state["current"], state["current"]["audience"]
        last = state["history_of"][aud][-1]
        text = template_explanation(state["facts"], aud)
        reason = f"the model's text failed the hallucination check {len(state['history_of'][aud])} time(s): " \
                 + "; ".join(last["violations"][:2])
        await self._trace(state).note("decision", f"fallback.{aud}", f"Template writer for the {aud}",
                                      "Nothing the model wrote was used. " + reason, status="warn")
        await self.publish({"type": "token", "runId": state["runId"], "audience": aud, "text": text, "attempt": "fallback"})
        return {"current": {**cur, "text": text, "writer": "template", "model": None, "fallbackReason": reason,
                            "attempt": cur["attempt"] + 1, "gen": None}}

    async def accept(self, state: RunState) -> dict[str, Any]:
        cur = state["current"]
        aud = cur["audience"]
        attempts = state["history_of"][aud]
        drafts = {**state["drafts"], aud: {**cur, "attempts": attempts,
                                          "drafts": sum(1 for a in attempts if a["writer"] == "omlx")}}
        nxt = state["cursor"] + 1
        route = "next" if nxt < len(AUDIENCES) else "done"
        return {"drafts": drafts, "cursor": nxt, "attempt": 0, "feedback": None, "route": route}

    async def judge(self, state: RunState) -> dict[str, Any]:
        judges: dict[str, Any] = {}
        if not judge_mod.wanted(self.model):
            return {"judges": judges}
        for aud, d in state["drafts"].items():
            if d["writer"] != "omlx":
                continue
            r = await judge_mod.judge(self.model, state["facts"], d["text"], state["measures"][aud])
            judges[aud] = r
            await self._trace(state).note(
                "llm", f"judge.{aud}", f"Second opinion on the {aud} text",
                "The same model is asked which sentences the facts do not support. Advisory only; the deterministic "
                "measurer decides.", status="ok" if r.get("agrees", True) else "warn", output=r, ms=r.get("ms", 0))
        return {"judges": judges}

    # -- artefacts ----------------------------------------------------------------------

    async def compose(self, state: RunState) -> dict[str, Any]:
        trace, app, run_id = self._trace(state), state["app"], state["runId"]
        facts, drafts, measures = state["facts"], state["drafts"], state["measures"]
        trace_doc = {"schema": "landchain.validation-trace/v1", "runId": run_id, "applicationId": app["appId"],
                     "steps": trace.as_list()}
        trace_cid = self.cids.put(canonical(trace_doc))
        report, cert_bytes = report_mod.assemble(
            app=app, parcel=state["parcel"], verdict=state["verdict"], checks=state["checks"], facts=facts,
            drafts=drafts, measures=measures, judges=state["judges"], trigger=state["trigger"],
            investigation=state.get("investigation") or {"ran": False}, reflections=state.get("reflections") or {},
            key_did=self.key.did, key_fingerprint=self.key.fingerprint, trace_cid=trace_cid, model_id=self.model.model,
            graph={"name": GRAPH_NAME, "version": GRAPH_VERSION, "nodes": self.NODES}, libraries=library_versions(),
            prompt_changed=state.get("promptChanged", False), notice=NOTICE)
        report_bytes = canonical(report)
        files = {"reportSha256": sha256_hex(report_bytes), "reportCid": self.cids.put(report_bytes),
                 "certificateCid": self.cids.put(cert_bytes), "traceCid": trace_cid, "bytes": len(report_bytes)}
        await trace.note("store", "compose", f"Wrote report {report['reportId']} and its certificate",
                         "Canonical JSON for machines, plain text for people. Both are stored by content identifier; "
                         "the certificate is rendered from the report, not written by a model.",
                         status="ok", output={**files, "reportId": report["reportId"]})
        return {"report": report, "files": files}

    async def sign(self, state: RunState) -> dict[str, Any]:
        t0 = time.monotonic()
        files, report = dict(state["files"]), state["report"]
        report_bytes, cert_bytes = self.cids.get(files["reportCid"]), self.cids.get(files["certificateCid"])
        out = {}
        for name, data in (("report", report_bytes), ("certificate", cert_bytes)):
            sig = self.key.pgp.sign_detached(data)
            check = verify_detached(self.key.public_armored, data, sig)
            if not check.valid:
                raise RuntimeError(f"self-check of the {name} signature failed: {check.reason}")
            out[f"{name}SignatureCid"] = self.cids.put(sig.encode())
        files.update(out)
        await self._trace(state).note(
            "sign", "pgp.sign", f"Signed report {report['reportId']} and its certificate (OpenPGP, Ed25519)",
            "Detached signatures anyone can verify with gpg or OpenPGP.js, no ledger access needed.", status="ok",
            output={"sha256": files["reportSha256"], "reportCid": files["reportCid"], "certificateCid": files["certificateCid"],
                    "keyFingerprint": self.key.fingerprint}, ms=int((time.monotonic() - t0) * 1000))
        return {"files": files}

    async def verify(self, state: RunState) -> dict[str, Any]:
        """Re-read what was stored, exactly as an outsider would, and run the independent verifier on it."""
        files, trace = state["files"], self._trace(state)
        t0 = time.monotonic()
        res = verifier.verify_report(
            self.cids.get(files["reportCid"]), self.cids.get(files["reportSignatureCid"]).decode(), self.key.public_armored,
            trace_bytes=self.cids.get(files["traceCid"]), certificate_bytes=self.cids.get(files["certificateCid"]),
            certificate_signature=self.cids.get(files["certificateSignatureCid"]).decode(), report_cid=files["reportCid"])
        failed = [f for f in res["findings"] if f["result"] == "fail"]
        await trace.note(
            "verify", "verify.independent", "Independent verification of the signed files",
            "A separate module that shares no code with the writer re-checks: JSON shape, canonical bytes, CID, signature "
            "and signer, verdict rule, authority, facts hash, both explanations re-measured, certificate, trace."
            + ("" if not failed else " FAILED: " + "; ".join(f["label"] for f in failed[:3])),
            status="ok" if res["passed"] else "fail", output={"summary": res["summary"], "findings": res["findings"]},
            ms=int((time.monotonic() - t0) * 1000))
        return {"verification": {"pre": res, "post": None}, "route": "anchor_report" if res["passed"] else "quarantine"}

    async def anchor_report(self, state: RunState) -> dict[str, Any]:
        t0 = time.monotonic()
        files, report, v = state["files"], state["report"], state["verdict"]
        res = await self.gw.submit("landrec", "SubmitValidationReport", state["appId"], files["reportSha256"],
                                   files["reportCid"], files["traceCid"], v["status"], str(v["flagCount"]), str(v["confidence"]))
        await self._trace(state).note(
            "anchor", "ledger.SubmitValidationReport", f"Anchored on the ledger in block {res.block}",
            "The report's SHA-256 and CIDs are now on the ledger under the agent's certificate (Org2MSP, role=agent). "
            "The application's status did not change.", status="ok",
            output={"txId": res.tx_id, "block": res.block, "endorsers": res.endorsers}, ms=int((time.monotonic() - t0) * 1000))
        return {"anchor": {"txId": res.tx_id, "block": res.block}}

    async def confirm(self, state: RunState) -> dict[str, Any]:
        """Ask the ledger what it now holds for this hash and compare with what we meant to anchor."""
        files, key = state["files"], self.key
        try:
            got = json.loads(await self.gw.evaluate("landrec", "VerifyValidationReport", files["reportSha256"]))
        except Exception as exc:
            got = {"anchored": False, "error": str(exc)[:160]}
        findings = [
            {"id": "ledger.anchored", "label": "The ledger holds this report's hash", "ok": bool(got.get("anchored"))},
            {"id": "ledger.cid", "label": "The anchored report CID is the one stored here",
             "ok": got.get("reportCid", got.get("cid")) in (None, files["reportCid"])},
            {"id": "ledger.app", "label": "It is anchored against this application",
             "ok": got.get("appId", state["appId"]) == state["appId"]},
        ]
        ok = all(f["ok"] for f in findings)
        await self._trace(state).note(
            "verify", "verify.ledger", "Read the anchor back from the ledger",
            "A fresh query, not the transaction receipt.", status="ok" if ok else "fail",
            output={"findings": findings, "ledger": {k: got.get(k) for k in ("anchored", "appId", "agent", "verdict", "at")}})
        v = dict(state["verification"])
        v["post"] = {"passed": ok, "findings": [{**f, "result": "pass" if f["ok"] else "fail"} for f in findings]}
        return {"verification": v}

    async def quarantine(self, state: RunState) -> dict[str, Any]:
        failed = [f["label"] for f in state["verification"]["pre"]["findings"] if f["result"] == "fail"]
        await self._trace(state).note(
            "decision", "quarantine", "Report not anchored: independent verification failed",
            "The files are kept for inspection but nothing was put on the ledger. " + "; ".join(failed[:3]), status="fail")
        return {"outcome": "quarantined", "error": None}

    async def finish(self, state: RunState) -> dict[str, Any]:
        return {}

    # ------------------------------------------------------------------ wiring

    def _build(self) -> StateGraph:
        g = StateGraph(RunState)
        for name in self.NODES:
            if name == "finish":
                continue
            g.add_node(name, self._node(name)(getattr(self, name)))
        g.add_node("finish", self.finish)  # runs even after an error, so it is not wrapped

        def stop_or(nxt: str) -> Callable[[RunState], str]:
            return lambda s: "finish" if (s.get("stop") or s.get("error")) else nxt

        g.add_edge(START, "plan")
        chain = ["plan", "gather", "run_checks", "investigate", "rule_verdict", "prepare", "draft"]
        for a, b in zip(chain, chain[1:]):
            g.add_conditional_edges(a, stop_or(b), {b: b, "finish": "finish"})
        g.add_conditional_edges("draft", stop_or("measure"), {"measure": "measure", "finish": "finish"})
        g.add_conditional_edges("measure", lambda s: "finish" if s.get("error") else
                                {"retry": "reflect", "fallback": "fallback", "accept": "accept"}[s["route"]],
                                {"reflect": "reflect", "fallback": "fallback", "accept": "accept", "finish": "finish"})
        g.add_conditional_edges("reflect", lambda s: "finish" if s.get("error") else s["route"],
                                {"draft": "draft", "fallback": "fallback", "finish": "finish"})
        g.add_conditional_edges("fallback", stop_or("measure"), {"measure": "measure", "finish": "finish"})
        g.add_conditional_edges("accept", lambda s: "finish" if s.get("error") else
                                ("draft" if s["route"] == "next" else "judge"),
                                {"draft": "draft", "judge": "judge", "finish": "finish"})
        for a, b in (("judge", "compose"), ("compose", "sign"), ("sign", "verify")):
            g.add_conditional_edges(a, stop_or(b), {b: b, "finish": "finish"})
        g.add_conditional_edges("verify", lambda s: "finish" if s.get("error") else s["route"],
                                {"anchor_report": "anchor_report", "quarantine": "quarantine", "finish": "finish"})
        g.add_conditional_edges("anchor_report", stop_or("confirm"), {"confirm": "confirm", "finish": "finish"})
        g.add_edge("confirm", "finish")
        g.add_edge("quarantine", "finish")
        g.add_edge("finish", END)
        return g

    def describe(self) -> dict[str, Any]:
        """For the dashboard: the real compiled graph, as nodes, edges and Mermaid."""
        drawn = self.graph.get_graph()
        return {"name": GRAPH_NAME, "version": GRAPH_VERSION, "libraries": library_versions(),
                "nodes": [{"id": n, "label": self.DESCRIPTIONS.get(n, n)} for n in self.NODES],
                "edges": [{"from": e.source, "to": e.target, "conditional": e.conditional} for e in drawn.edges],
                "mermaid": drawn.draw_mermaid(),
                "subgraphs": {"investigate": inv_mod.describe_loop(self.model)}}
