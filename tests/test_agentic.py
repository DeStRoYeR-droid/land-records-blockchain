"""The agent loop: reading tool calls, the budget, refusing bad calls, and grounding findings."""

import asyncio
import json

from langchain_core.messages import AIMessage
from langchain_core.tools import StructuredTool

from agent.agentic import AgentLoop, parse_calls
from agent.grounding import ground
from agent.investigation import finish_tool
from agent.llm import Generation


def test_parse_native_text_and_fenced_calls():
    native = AIMessage(content="", tool_calls=[{"id": "x", "name": "a", "args": {"k": 1}}])
    assert [(c.name, c.args) for c in parse_calls(native)[0]] == [("a", {"k": 1})]
    qwen = AIMessage(content='<think>hmm</think>Let me look.\n<tool_call>\n{"name": "b", "arguments": {"s": "SY-1"}}\n</tool_call>')
    calls, visible = parse_calls(qwen)
    assert [(c.name, c.args) for c in calls] == [("b", {"s": "SY-1"})] and visible == "Let me look."
    fenced = AIMessage(content='```json\n{"name": "c", "parameters": {"n": 2}}\n```')
    assert [(c.name, c.args) for c in parse_calls(fenced)[0]] == [("c", {"n": 2})]
    stringy = AIMessage(content='<tool_call>{"name": "d", "arguments": "{\\"q\\": \\"x\\"}"}</tool_call>')
    assert parse_calls(stringy)[0][0].args == {"q": "x"}
    assert parse_calls(AIMessage(content="Just prose."))[0] == []


class Scripted:
    """A model that replays a list of replies and records what it was offered."""
    protocol = "native"
    model = "scripted"

    def __init__(self, replies):
        self.replies = list(replies)
        self.offered = []

    async def think(self, messages, tools, max_tokens=400, temperature=0.1):
        self.offered.append([t.name for t in tools])
        r = self.replies.pop(0) if self.replies else AIMessage(content="nothing more")
        return r, Generation("", 5, 1, 1, True)


def tool(name, result):
    async def fn(survey_no: str) -> dict:
        return {"surveyNo": survey_no, **result}
    return StructuredTool.from_function(coroutine=fn, name=name, description=name)


def call(name, **args):
    return AIMessage(content="", tool_calls=[{"id": f"id-{name}", "name": name, "args": args}])


def run(loop, brief="go"):
    return asyncio.run(loop.run(brief))


def test_loop_runs_tools_then_finishes():
    m = Scripted([call("chain", survey_no="SY-1"),
                  call("finish", summary="done", findings=[{"claim": "The parcel SY-1 has had 3 owners.",
                                                            "severity": "info", "evidence": ["c1"]}])])
    loop = AgentLoop(m, "t", "sys", [tool("chain", {"owners": 3})], finish_tool(), max_calls=3, max_turns=5)
    res = run(loop)
    assert res.stop == "finished" and len(res.calls) == 1 and res.calls[0]["ok"]
    assert res.final["findings"][0]["evidence"] == ["c1"]
    calls = {c["id"]: c for c in res.calls}
    assert ground(res.final["findings"][0], calls, {}) == []


def test_bad_calls_are_refused_and_counted():
    m = Scripted([call("approve_transfer", survey_no="SY-1"),        # no such tool
                  call("chain"),                                     # missing argument
                  call("chain", survey_no="SY-1"),
                  call("chain", survey_no="SY-1"),                   # a repeat
                  call("finish", summary="s", findings=[])])
    res = run(AgentLoop(m, "t", "sys", [tool("chain", {"owners": 1})], finish_tool(), max_calls=6, max_turns=8))
    oks = [c["ok"] for c in res.calls]
    assert oks == [False, False, True, False] and res.errors == 3 and res.stop == "finished"
    assert "no tool named approve_transfer" in json.dumps(res.calls[0]["observation"])


def test_budget_forces_finish_and_streak_stops():
    m = Scripted([call("chain", survey_no=f"SY-{i}") for i in range(10)])
    res = run(AgentLoop(m, "t", "sys", [tool("chain", {"owners": 1})], finish_tool(), max_calls=2, max_turns=10))
    assert len([c for c in res.calls if c["ok"]]) == 2
    assert m.offered[-1] == ["finish"] and res.stop == "budget used up"
    prose = Scripted([AIMessage(content="I think it is fine.")] * 5)
    res = run(AgentLoop(prose, "t", "sys", [tool("chain", {})], finish_tool(), max_calls=3, max_turns=8))
    assert res.stop == "no usable tool call" and res.final is None


def test_grounding_refuses_invented_figures_and_decisions():
    calls = {"c1": {"ok": True, "observation": {"medianPricePerSqm": 5200, "thisFilePricePerSqm": 2655,
                                                 "percentOfMedian": 51, "approvedSalesNearby": 4}},
             "c2": {"ok": False, "observation": {"error": "x"}}}
    good = {"claim": "The price of Rs. 2,655 per sq.m is 51% of the median of Rs. 5,200 for 4 approved sales.",
            "evidence": ["c1"]}
    assert ground(good, calls, {}) == []
    assert ground({**good, "claim": good["claim"] + " It should be rejected."}, calls, {})
    assert ground({**good, "evidence": ["c9"]}, calls, {})
    assert ground({**good, "evidence": ["c2"]}, calls, {})
    assert ground({"claim": "The seller sold 7 other parcels for Rs. 99,99,999.", "evidence": ["c1"]}, calls, {})


def test_finish_accepts_what_small_models_write():
    from agent.investigation import FinishArgs
    got = FinishArgs.model_validate({"findings": ["Price is 20% of the median (c2)",
                                                  {"claim": "Overlaps SY-9 by 40 sq.m", "evidence": "call c1", "severity": "HIGH"}]})
    assert [f.evidence for f in got.findings] == [["c2"], ["c1"]] and got.findings[1].severity == "concern"
