"""
Ask the agent: a tool-using LangGraph for questions about LandChain, a parcel, a file or a run.

    START -> route --model up--> agent (the agent loop: think <-> act) -> check -> END
                   \\--offline--> retrieve --------------------------------------> END

agent     the model looks things up with read-only tools (the architecture
          notes, parcels, chains of ownership, transfer files, the run being
          viewed, the network) and answers in plain text when it has enough
check     every number and identifier in the answer must appear in what the
          tools returned (or in the question); anything that does not is
          listed under the answer as not verified
retrieve  with no model: the most relevant sections of the notes, as they are

Same rule as everywhere in the validator: the model chooses and writes, code checks.
"""

from __future__ import annotations

import json
from typing import Any, Awaitable, Callable, TypedDict

import httpx
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import StructuredTool
from langgraph.graph import END, START, StateGraph

from nodes.sdk import Gateway

from . import prompts, quality, settings
from .agentic import AgentLoop
from .explainer import Explainer, knowledge_sections, select_sections

TokenFn = Callable[[str], Awaitable[None]]
EventFn = Callable[[dict[str, Any]], Awaitable[None]]


class AskState(TypedDict, total=False):
    question: str
    run_context: str | None
    sections: list[tuple[str, str]]
    text: str
    engine: str
    model: str | None
    problems: list[str]
    calls: list[dict[str, Any]]
    stop: str | None


def _initials(name: str) -> str:
    return " ".join(f"{p[0].upper()}." for p in str(name or "").split() if p[:1].isalpha())


def build_tools(gw: Gateway, http: httpx.AsyncClient, run_context: str | None) -> list[StructuredTool]:
    async def q(fn: str, *args: str) -> Any:
        return json.loads(await gw.evaluate("landrec", fn, *args))

    async def search_notes(query: str) -> dict[str, Any]:
        """The architecture notes: how LandChain and the validator work."""
        return {"sections": [{"title": t, "text": b[:700]} for t, b in select_sections(query, k=2)]}

    async def read_parcel(survey_no: str) -> dict[str, Any]:
        """A parcel on the ledger: place, area, boundary area, status, current owner (initials)."""
        p = await q("ReadParcel", survey_no)
        return {"surveyNo": p["surveyNo"], "village": p["village"], "district": p["district"], "areaSqm": p["areaSqm"],
                "boundaryAreaSqm": p.get("boundaryAreaSqm"), "status": p["status"], "owner": _initials(p["ownerName"]),
                "pendingApplication": p.get("pendingApplication"), "registeredAt": (p.get("registeredAt") or "")[:10]}

    async def ownership_chain(survey_no: str) -> dict[str, Any]:
        """Every owner a parcel has had (initials), oldest first."""
        c = await q("OwnershipChain", survey_no)
        return {"surveyNo": survey_no, "owners": c["owners"],
                "links": [{"seq": l["seq"], "owner": _initials(l["ownerName"]), "acquired": l["acquired"],
                           "appId": l.get("appId"), "since": (l["since"] or "")[:10]} for l in c["links"]]}

    async def read_application(app_id: str) -> dict[str, Any]:
        """A transfer file: status, parcel, each step, the validator's verdicts."""
        a = await q("ReadApplication", app_id)
        return {"appId": a["appId"], "surveyNo": a["surveyNo"], "status": a["status"],
                "steps": [{"at": t["at"][:16], "action": t["action"], "role": t["role"]} for t in a["trail"]][-8:],
                "verdicts": [v["verdict"] for v in a.get("validations", [])],
                "decision": (a.get("decision") or {}).get("outcome")}

    async def parcels_in_village(village: str) -> dict[str, Any]:
        """Parcels in a village, from the geospatial database (survey numbers, areas, status)."""
        r = await http.get(f"{settings.GATEWAY_URL}/api/public/geo/parcels", params={"village": village, "limit": 40},
                           timeout=8)
        feats = r.json().get("features", [])
        return {"village": village, "count": len(feats), "parcels": [
            {"surveyNo": f["id"], "areaSqm": f["properties"]["areaSqm"], "status": f["properties"]["status"],
             "owners": f["properties"]["owners"]} for f in feats[:25]]}

    async def network_status() -> dict[str, Any]:
        """The parcels, villages and transfer files on the register, by status."""
        r = await http.get(f"{settings.GATEWAY_URL}/api/public/summary", timeout=8)
        d = r.json()
        return {k: d.get(k) for k in ("parcels", "villages", "applications", "byStatus", "height", "reports", "verdicts")}

    async def current_run() -> dict[str, Any]:
        """The validator run the officer is looking at (if any): verdict and check results."""
        return json.loads(run_context) if run_context else {"error": "no run is open"}

    specs = [(search_notes, "Search the architecture notes (how LandChain and its validator work)."),
             (read_parcel, "Read a parcel by survey number."), (ownership_chain, "A parcel's chain of owners."),
             (read_application, "Read a transfer file by application id (APP-...)."),
             (parcels_in_village, "Parcels in a village (geospatial database)."),
             (network_status, "Counts of parcels and files on the register."),
             (current_run, "The validator run currently open on the officer's screen.")]
    return [StructuredTool.from_function(coroutine=fn, name=fn.__name__, description=d) for fn, d in specs]


def build(model: Explainer, gw: Gateway | None = None, http: httpx.AsyncClient | None = None):
    async def route(s: AskState) -> dict[str, Any]:
        await model.probe()
        return {"engine": "omlx" if (model.mode != "template" and model.reachable and gw is not None) else "retrieval-only"}

    async def retrieve(s: AskState, config: RunnableConfig) -> dict[str, Any]:
        on_token: TokenFn = config["configurable"]["on_token"]
        sections = select_sections(s["question"], k=3)
        text = ("The language model is offline, so here are the most relevant parts of the architecture notes:\n\n"
                + "\n\n".join(f"{t}: {b}" for t, b in sections))
        await on_token(text)
        return {"sections": sections, "text": text, "model": None, "problems": [], "calls": []}

    async def agent(s: AskState, config: RunnableConfig) -> dict[str, Any]:
        on_token: TokenFn = config["configurable"]["on_token"]
        on_event: EventFn | None = config["configurable"].get("on_event")
        question = s["question"] + ("\n(The officer has a validator run open; current_run shows it.)" if s.get("run_context") else "")
        loop = AgentLoop(model, "ask", prompts.ASK_AGENT_SYSTEM, build_tools(gw, http, s.get("run_context")), None,
                         max_calls=4, max_turns=5, emit=on_event, max_tokens=350)
        res = await loop.run(question)
        text = (res.final or {}).get("text") or ("I could not find an answer with the tools I have."
                                                 if res.stop != "answered" else "")
        if res.stop.startswith("model error"):
            text = f"The language model failed to answer ({res.stop[13:120]})."
        for word in text.split(" "):                    # the answer arrives in one piece; stream it for the reader
            await on_token(word + " ")
        titles = [sec["title"] for c in res.calls if c["name"] == "search_notes" and c["ok"]
                  for sec in (c.get("observation") or {}).get("sections", [])]
        secs = knowledge_sections()
        return {"text": text.strip(), "model": model.model, "calls": res.calls, "stop": res.stop,
                "sections": [(t, secs.get(t, "")) for t in dict.fromkeys(titles)]}

    async def check(s: AskState) -> dict[str, Any]:
        source = {"q": s["question"], "observations": [c.get("observation") for c in s.get("calls", [])],
                  "run": s.get("run_context") or ""}
        idx = quality.FactIndex({"text": json.dumps(source, ensure_ascii=False, default=str)})
        claims, _ = quality._entities(s.get("text", ""), idx)  # noqa: SLF001 -- the measurer's entity check
        return {"problems": [c["text"] for c in claims if not c["supported"] and c["kind"] in ("number", "identifier", "date")]}

    g = StateGraph(AskState)
    g.add_node("route", route)
    g.add_node("retrieve", retrieve)
    g.add_node("agent", agent)
    g.add_node("check", check)
    g.add_edge(START, "route")
    g.add_conditional_edges("route", lambda s: "agent" if s["engine"] == "omlx" else "retrieve",
                            {"agent": "agent", "retrieve": "retrieve"})
    g.add_edge("retrieve", END)
    g.add_edge("agent", "check")
    g.add_edge("check", END)
    return g.compile()
