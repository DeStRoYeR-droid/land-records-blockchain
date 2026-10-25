#!/usr/bin/env python3
"""
A stand-in for oMLX, for machines without Apple Silicon (and for tests).

Speaks the same OpenAI-compatible API the validator uses (GET /v1/models,
POST /v1/chat/completions with streaming), emits a <think> block like Qwen3,
and writes a plain restatement of the CHECK RESULTS it is sent.

    python scripts/dev/fake_omlx.py                 # port 18000
    OMLX_BASE_URL=http://127.0.0.1:18000/v1 ./startup.sh

FAKE_OMLX_MODE=good         grounded text (default)
FAKE_OMLX_MODE=hallucinate  invents an amount   -> the hallucination measurer must reject it
FAKE_OMLX_MODE=verdict      recommends approval -> ... the decision-language rule
FAKE_OMLX_MODE=name         invents a person; =count a wrong count; =stance contradicts the verdict
FAKE_OMLX_MODE=offline      every call returns 503
FAKE_OMLX_MODE=flaky        flawed first draft, clean redraft -> exercises the retry loop

Tool calling (the investigating agent and the Ask box), like oMLX serving Qwen:
FAKE_OMLX_TOOLS=native      OpenAI `tool_calls` in the reply (default)
FAKE_OMLX_TOOLS=text        <tool_call>{...}</tool_call> blocks in the text (a server that does not parse them)
FAKE_OMLX_TOOLS=refuse      400 for any request that carries `tools` -> the agent must switch to the text protocol
Agent fault modes (FAKE_OMLX_MODE): liar (a finding the results do not support), loop (repeats one call),
badtool (an unknown tool, then bad arguments), noisy (prose instead of tool calls)
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import time

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

MODEL = os.environ.get("FAKE_OMLX_MODEL", "mlx-community/Qwen3-4B-Instruct-2507-4bit")
KEY = os.environ.get("FAKE_OMLX_KEY", "")
app = FastAPI(title="fake oMLX")


BAD_MODES = {
    "hallucinate": " The market value is Rs. 99,99,999.",            # invents an amount
    "verdict": " This application should be approved.",              # recommends a decision
    "name": " It was reviewed by Sri Venkata Rangarajan.",           # invents a person
    "count": " In total 7 checks failed.",                            # wrong count
    "stance": " Everything we checked is in order.",                  # contradicts a non-clear verdict
}


def compose(messages: list[dict]) -> str:
    """Restates the CHECK RESULTS it is sent. FAKE_OMLX_MODE picks a flaw to add (see BAD_MODES), or:
    good (default) | flaky (flawed on the first draft, clean once the prompt carries the checker's complaint)."""
    mode = STATE["mode"]
    user = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")
    system = next((m["content"] for m in messages if m["role"] == "system"), "")
    if "THE CHECKER SAID" in user:                             # the reflect step
        action = "template" if mode == "reflect_template" else "redraft"
        return json.dumps({"action": action, "fix": "Leave out what the checker named and keep to the facts."})
    if "SENTENCES:" in user:                                   # the LLM judge
        if mode in BAD_MODES or mode == "flaky":
            n = len(re.findall(r"^\d+\. ", user, re.M))
            return f'{{"unsupported": [{n}]}}'
        return '{"unsupported": []}'
    if "CHECK RESULTS" not in user:                            # an /ask question
        return ("<think>The context says the validator only explains.</think>"
                "The validator gathers evidence and explains fixed-rule results; the Collector makes the decision.")
    head = re.search(r"Application (\S+) for parcel (\S+)", user)
    verdict = re.search(r"Verdict by rule: (\w+)", user)
    bad = []
    for section in ("FAILED", "NEEDS ATTENTION"):
        m = re.search(rf"^{section}:\n((?:- .+\n?)+)", user, re.M)
        if m:
            for line in m.group(1).strip().splitlines():
                label, _, rest = line[2:].partition(": ")
                detail = re.sub(r" \(evidence:.*$", "", rest).rstrip(".")
                bad.append(f"{label} ({detail})")
    passed = re.search(r"^PASSED: (.+)$", user, re.M)
    n_pass = len(passed.group(1).split("; ")) if passed else 0
    skipped = re.search(r"^NOT RUN: (.+)$", user, re.M)
    n_skip = len(skipped.group(1).split("; ")) if skipped else 0
    total = len(bad) + n_pass + n_skip
    citizen = "TASK: Write 2 to 4 short sentences for the citizen" in user
    app_id, survey = (head.group(1), head.group(2).rstrip(".")) if head else ("the application", "the parcel")
    if citizen:
        text = (f"We checked your transfer of parcel {survey}. "
                + ("Everything we checked is in order. " if not bad else "Some items need an officer's review. ")
                + "An officer reviews it next and the Collector makes the final decision.")
    else:
        tally = f"{n_pass} passed" + (f" and {n_skip} could not be run" if n_skip else "")
        text = (f"The validator ran {total} checks on application {app_id} for parcel {survey}; {tally}. "
                + ("" if not bad else "It found: " + "; ".join(bad) + ". ")
                + f"The verdict is {verdict.group(1) if verdict else 'unknown'} by the fixed rule, "
                  "and the decision belongs to the Collector.")
    retry = "previous answer was rejected" in user
    if mode in BAD_MODES or (mode in ("flaky", "reflect_template") and not retry):
        text += BAD_MODES.get(mode, BAD_MODES["hallucinate"])
    return "<think>Restate the rule results only. Do not decide.</think>\n\n" + text


STATE = {"mode": os.environ.get("FAKE_OMLX_MODE", "good"), "delay": float(os.environ.get("FAKE_OMLX_DELAY", "0.015")),
         "tools": os.environ.get("FAKE_OMLX_TOOLS", "native"), "requests": 0, "toolRequests": 0}


# ---------------------------------------------------------------------------
# Tool-calling turns
# ---------------------------------------------------------------------------

def _text(m: dict) -> str:
    c = m.get("content")
    return c if isinstance(c, str) else "" if c is None else json.dumps(c)


def _loads(text: str) -> dict:
    try:
        return json.loads(text)
    except ValueError:                                  # a result cut short by the agent's size limit
        m = re.search(r'"call":"(c\d+)"', text)
        return {"call": m.group(1) if m else "c?", "result": {}}


def history(messages: list[dict]) -> list[tuple[str, dict, dict]]:
    """(tool name, args, result) for every call so far, from either protocol."""
    calls: list[tuple[str, dict]] = []
    results: list[dict] = []
    for m in messages:
        if m["role"] == "assistant":
            for tc in m.get("tool_calls") or []:
                calls.append((tc["function"]["name"], json.loads(tc["function"].get("arguments") or "{}")))
            for block in re.findall(r"<tool_call>\s*(\{.*?\})\s*</tool_call>", _text(m), re.S):
                obj = json.loads(block)
                calls.append((obj["name"], obj.get("arguments") or {}))
        elif m["role"] == "tool":
            results.append(_loads(_text(m)))
        elif m["role"] == "user":
            for block in re.findall(r"<tool_response>\s*(\{.*?\})\s*</tool_response>", _text(m), re.S):
                results.append(_loads(block))
    return [(n, a, r) for (n, a), r in zip(calls, results)]


def investigator_turn(messages: list[dict]) -> tuple[str, list[dict]]:
    """(visible text, tool calls) for one investigator turn: a scripted but result-driven policy."""
    mode = STATE["mode"]
    brief = next(_text(m) for m in messages if m["role"] == "user")
    survey = (re.search(r"parcel (\S+) in ", brief) or [None, "SY-1"])[1]
    done = history(messages)
    names = [n for n, _, _ in done]
    budget_msg = any("used your budget" in _text(m) for m in messages if m["role"] == "user")
    if mode == "noisy":
        return "The documents look fine to me and I think the file is in order.", []
    if mode == "loop":
        return "Let me look at the prices again.", [{"name": "nearby_sale_prices", "arguments": {"radius_m": 1500}}]
    if mode == "badtool" and len(done) < 2 and "approve_transfer" not in names:
        if not done:
            return "I will approve this transfer directly.", [{"name": "approve_transfer", "arguments": {"app_id": "x"}}]
    if mode == "badtool" and len(done) == 1:
        return "Checking the neighbour's files.", [{"name": "party_other_files", "arguments": {"party": "neighbour"}}]
    plan = ["nearby_sale_prices", "party_other_files", "parcel_ownership_chain"]
    if "overlaps this one" in brief:
        plan = ["parcel_boundary_overlaps", "nearby_sale_prices", "party_other_files"]
    args = {"nearby_sale_prices": {"radius_m": 1500}, "party_other_files": {"party": "seller"},
            "parcel_ownership_chain": {"survey_no": survey}, "parcel_boundary_overlaps": {"survey_no": survey}}
    todo = [p for p in plan if p not in names]
    if todo and not budget_msg:
        nxt = todo[0]
        return f"Next I check {nxt.replace('_', ' ')}.", [{"name": nxt, "arguments": args[nxt]}]
    findings = []
    ok = {r["call"]: r["result"] for _, _, r in done if "error" not in (r.get("result") or {})}
    for n, _, r in done:
        cid, res = r["call"], r.get("result") or {}
        if cid not in ok:
            continue
        if n == "parcel_boundary_overlaps" and res.get("overlaps"):
            o = res["overlaps"][0]
            findings.append({"claim": f"The boundary overlaps {o['surveyNo']} by {o['overlapSqm']} sq.m.",
                             "severity": "concern", "evidence": [cid]})
        if n == "nearby_sale_prices" and res.get("medianPricePerSqm"):
            pct = res["percentOfMedian"]
            claim = (f"This file's price of Rs. {res['thisFilePricePerSqm']} per sq.m is {pct}% of the median of "
                     f"Rs. {res['medianPricePerSqm']} per sq.m for {res['approvedSalesNearby']} approved sales within "
                     f"{int(res['radiusM'])} m.")
            findings.append({"claim": claim, "severity": "concern" if pct < 60 else "info", "evidence": [cid]})
        if n == "party_other_files" and res.get("otherFiles"):
            findings.append({"claim": f"The seller is a party to {res['otherFiles']} other transfer files on the ledger.",
                             "severity": "concern" if res["asSeller"] >= 2 else "info", "evidence": [cid]})
        if n == "parcel_ownership_chain" and res.get("owners"):
            findings.append({"claim": f"The parcel {survey} has had {res['owners']} owner(s) on the ledger.",
                             "severity": "info", "evidence": [cid]})
    if mode == "liar":
        findings.append({"claim": "The seller sold 7 other parcels in 2025 for Rs. 99,99,999.", "severity": "concern",
                         "evidence": [next(iter(ok), "c1")]})
    summary = f"I looked at {', '.join(n.replace('_', ' ') for n in names) or 'nothing'}."
    return "I have what I need.", [{"name": "finish", "arguments": {"summary": summary, "findings": findings}}]


def ask_turn(messages: list[dict]) -> tuple[str, list[dict]]:
    question = next(_text(m) for m in messages if m["role"] == "user")
    done = history(messages)
    if not done:
        m = re.search(r"\b(SY-[A-Za-z0-9/.-]+|APP-[A-Za-z0-9-]+)", question)
        if m and m.group(1).startswith("SY-"):
            return "", [{"name": "read_parcel", "arguments": {"survey_no": m.group(1).rstrip(".?")}}]
        if m:
            return "", [{"name": "read_application", "arguments": {"app_id": m.group(1).rstrip(".?")}}]
        return "", [{"name": "search_notes", "arguments": {"query": question[:120]}}]
    n, _, r = done[-1]
    res = r.get("result") or {}
    if n == "read_parcel" and "surveyNo" in res:
        return (f"Parcel {res['surveyNo']} is in {res['village']}, {res['district']}: {res['areaSqm']} sq.m on record, "
                f"status {res['status']}."), []
    if n == "read_application" and "appId" in res:
        return f"Application {res['appId']} for parcel {res['surveyNo']} is {res['status']}.", []
    secs = res.get("sections") or []
    if secs:
        first = secs[0]["text"].split(". ")[0]
        return f"From the notes ({secs[0]['title']}): {first}.", []
    return "I don't know; the tools did not return an answer.", []


def tool_reply(body: dict) -> dict:
    messages = body["messages"]
    system = next((_text(m) for m in messages if m["role"] == "system"), "")
    text, calls = investigator_turn(messages) if "investigator of LandChain" in system else ask_turn(messages)
    use_text = STATE["tools"] in ("text", "refuse") or not body.get("tools")
    msg: dict = {"role": "assistant", "content": text}
    if calls and use_text:
        msg["content"] = (text + "\n" if text else "") + "\n".join(
            f"<tool_call>\n{json.dumps(c)}\n</tool_call>" for c in calls)
    elif calls:
        msg["content"] = text or None
        msg["tool_calls"] = [{"id": f"call_{STATE['toolRequests']}_{i}", "type": "function",
                              "function": {"name": c["name"], "arguments": json.dumps(c["arguments"])}}
                             for i, c in enumerate(calls)]
    return {"id": "fake", "object": "chat.completion", "model": MODEL,
            "choices": [{"index": 0, "message": msg, "finish_reason": "tool_calls" if calls and not use_text else "stop"}],
            "usage": {"prompt_tokens": sum(len(_text(m)) for m in messages) // 4, "completion_tokens": 40,
                      "total_tokens": 0}}


@app.post("/__control")
async def control(request: Request):
    """Test hook: {"mode": "...", "delay": seconds-per-token}. Not part of the oMLX API."""
    body = await request.json()
    STATE.update({k: body[k] for k in ("mode", "delay", "tools") if k in body})
    return STATE


@app.get("/v1/models")
async def models(request: Request):
    if STATE["mode"] == "offline":
        return JSONResponse({"error": "offline"}, status_code=503)
    if KEY and request.headers.get("authorization") != f"Bearer {KEY}":
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    return {"object": "list", "data": [{"id": MODEL, "object": "model"}]}


@app.post("/v1/chat/completions")
async def chat(request: Request):
    if KEY and request.headers.get("authorization") != f"Bearer {KEY}":
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    if STATE["mode"] == "offline":
        return JSONResponse({"error": "offline"}, status_code=503)
    body = await request.json()
    STATE["requests"] += 1
    system = next((m.get("content") or "" for m in body["messages"] if m["role"] == "system"), "")
    if body.get("tools") and STATE["tools"] == "refuse":
        return JSONResponse({"error": "tools are not supported by this server"}, status_code=400)
    if body.get("tools") or "TOOLS. To call one" in system:
        STATE["toolRequests"] += 1
        await asyncio.sleep(STATE["delay"] * 20)
        return tool_reply(body)
    text = compose(body["messages"])
    if not body.get("stream"):
        return {"id": "fake", "object": "chat.completion", "model": MODEL,
                "choices": [{"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": "stop"}]}

    async def gen():
        for piece in re.findall(r"\S+\s*|\s+", text):
            chunk = {"id": "fake", "object": "chat.completion.chunk", "created": int(time.time()), "model": MODEL,
                     "choices": [{"index": 0, "delta": {"content": piece}, "finish_reason": None}]}
            yield f"data: {json.dumps(chunk)}\n\n"
            await asyncio.sleep(STATE["delay"])
        yield "data: [DONE]\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get("FAKE_OMLX_PORT", "18000")), log_level="warning")
