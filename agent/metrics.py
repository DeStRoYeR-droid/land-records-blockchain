"""
Quality metrics over all of the validator's runs: how often the model's text is
accepted, how faithful it is, how often it is redrafted or replaced, how fast
the model is, where the time goes, and how often independent verification passes.

Computed from the stored run records on request (cheap: tens to hundreds of runs).
"""

from __future__ import annotations

import statistics
from typing import Any

BINS = [(0.0, 0.5), (0.5, 0.7), (0.7, 0.8), (0.8, 0.9), (0.9, 1.01)]


def _hist(values: list[float]) -> list[dict[str, Any]]:
    return [{"from": lo, "to": min(hi, 1.0), "count": sum(1 for v in values if lo <= v < hi)} for lo, hi in BINS]


def compute(runs: list[dict[str, Any]]) -> dict[str, Any]:
    scored = [r for r in runs if (r.get("quality") or {}).get("aggregate")]
    faith: dict[str, list[float]] = {"officer": [], "citizen": []}
    rate: list[float] = []
    writers = {"omlx": 0, "template": 0}
    retries = fallbacks = injections = 0
    judged = agreed = 0
    speeds: list[float] = []
    node_ms: dict[str, list[int]] = {}
    for r in scored:
        q = r["quality"]
        for aud in faith:
            s = ((q.get("hallucination") or {}).get(aud) or {}).get("scores")
            if s:
                faith[aud].append(s["faithfulness"])
                rate.append(s["hallucinationRate"])
            g = (q.get("generation") or {}).get(aud) or {}
            if g.get("writer") in writers:
                writers[g["writer"]] += 1
            if g.get("tokensPerSecond"):
                speeds.append(g["tokensPerSecond"])
            j = (q.get("judge") or {}).get(aud)
            if j and j.get("usable"):
                judged += 1
                agreed += 1 if j.get("agrees") else 0
        agg = q["aggregate"]
        retries += agg.get("retries", 0)
        fallbacks += agg.get("fallbacks", 0)
        injections += (q.get("promptInjection") or {}).get("hits", 0)
    for r in runs:
        for node, ms in (r.get("timings") or {}).items():
            node_ms.setdefault(node, []).append(ms)

    # the agent's own choices: what the investigating model did with its budget
    inv = [r["investigation"] for r in runs if (r.get("investigation") or {}).get("ran")]
    tool_use: dict[str, int] = {}
    stops: dict[str, int] = {}
    for i in inv:
        for c in i.get("calls") or []:
            if c.get("ok"):                           # refused requests never ran
                tool_use[c["name"]] = tool_use.get(c["name"], 0) + 1
        stops[i.get("stop") or "?"] = stops.get(i.get("stop") or "?", 0) + 1
    n_calls = [len(i.get("calls") or []) for i in inv]
    accepted = sum(len(i.get("findings") or []) for i in inv)
    refused = sum(len(i.get("refused") or []) for i in inv)
    concerns = sum(1 for i in inv for f in i.get("findings") or [] if f.get("severity") == "concern")
    raised = sum(1 for r in runs if any(c["id"] == "agent_investigation" and c["status"] == "warn" for c in r.get("checks") or [])
                 and (r.get("verdict") or {}).get("failed", 0) == 0 and (r.get("verdict") or {}).get("attention") == 1)
    reflections = [e for r in scored for a in ("officer", "citizen")
                   for e in (((r["quality"].get("reflections") or {}).get(a)) or [])]
    agentic = {"investigations": len(inv), "toolCalls": sum(n_calls),
               "avgToolCalls": round(statistics.fmean(n_calls), 2) if n_calls else None,
               "invalidCalls": sum(1 for i in inv for c in i.get("calls") or [] if not c.get("ok")),
               "toolUse": dict(sorted(tool_use.items(), key=lambda kv: -kv[1])), "stops": stops,
               "findingsAccepted": accepted, "findingsRefused": refused, "concerns": concerns,
               "groundedShare": round(accepted / (accepted + refused), 3) if accepted + refused else None,
               "filesRaisedOnlyByAgent": raised,
               "reflections": len(reflections),
               "reflectChoices": {k: sum(1 for e in reflections if e.get("action") == k) for k in ("redraft", "template")},
               "reflectByModel": sum(1 for e in reflections if e.get("chosenBy") == "model")}

    verified = [r for r in runs if (r.get("verification") or {}).get("pre")]
    all_faith = faith["officer"] + faith["citizen"]
    texts = writers["omlx"] + writers["template"]
    recent = [{"runId": r["runId"], "appId": r["appId"], "at": r.get("finishedAt"), "verdict": (r.get("verdict") or {}).get("status"),
               "minFaithfulness": r["quality"]["aggregate"]["minFaithfulness"],
               "retries": r["quality"]["aggregate"]["retries"], "fallbacks": r["quality"]["aggregate"]["fallbacks"],
               "durationMs": r.get("durationMs"), "status": r["status"]} for r in scored[:30]]
    return {
        "runsMeasured": len(scored),
        "texts": texts,
        "faithfulness": {"mean": round(statistics.fmean(all_faith), 3) if all_faith else None,
                         "min": min(all_faith) if all_faith else None,
                         "officerMean": round(statistics.fmean(faith["officer"]), 3) if faith["officer"] else None,
                         "citizenMean": round(statistics.fmean(faith["citizen"]), 3) if faith["citizen"] else None,
                         "histogram": _hist(all_faith)},
        "hallucinationRate": {"mean": round(statistics.fmean(rate), 3) if rate else None, "max": max(rate) if rate else None},
        "writers": {**writers, "modelAcceptedShare": round(writers["omlx"] / texts, 3) if texts else None},
        "retries": retries, "fallbacks": fallbacks, "promptInjectionHits": injections,
        "judge": {"compared": judged, "agreementShare": round(agreed / judged, 3) if judged else None},
        "model": {"samples": len(speeds), "avgTokensPerSecond": round(statistics.fmean(speeds), 1) if speeds else None,
                  "medianTokensPerSecond": round(statistics.median(speeds), 1) if speeds else None},
        "verification": {"runs": len(verified),
                         "independentPassShare": round(sum(1 for r in verified if r["verification"]["pre"]["passed"]) / len(verified), 3)
                         if verified else None,
                         "quarantined": sum(1 for r in runs if r.get("status") == "quarantined"),
                         "ledgerConfirmed": sum(1 for r in verified if (r["verification"].get("post") or {}).get("passed"))},
        "nodeLatencyMs": {n: {"avg": int(statistics.fmean(v)), "max": max(v), "runs": len(v)} for n, v in node_ms.items()},
        "recent": recent,
        "agentic": agentic,
    }
