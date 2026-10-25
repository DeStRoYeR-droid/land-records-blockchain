"""
The agent loop: a small model that chooses its own tools, as a LangGraph.

    START -> think --tool calls--> act --+
               ^                         |
               +-------------------------+
             think --finish / answer / budget / error--> END

think   the model reads the conversation so far and either asks for one or
        more tools (OpenAI tool calls, or Qwen's <tool_call> blocks when the
        server cannot take tools), or finishes.
act     every requested call is checked before it runs: the tool must exist,
        its arguments must match the tool's schema, it must not repeat an
        earlier call, and the budget must allow it. The result goes back to
        the model labelled with a call id ("c3"), which findings must cite.

The loop is bounded: at most `max_calls` tool calls and `max_turns` model
turns; when either runs out, the model gets one last turn in which `finish`
is the only tool. Three invalid turns in a row (no usable call, unknown tool,
bad arguments, repeats) stop it. Every turn and call goes into the run's
trace and onto the live stream.

This module knows nothing about land: investigation.py and qa.py give it
their tools and prompts.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, TypedDict

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import BaseTool
from langgraph.graph import END, START, StateGraph
from pydantic import ValidationError

from nodes.common import log

from .llm import Model

logger = log.get("agentic")
Emit = Callable[[dict[str, Any]], Awaitable[None]]
THINK_RE = re.compile(r"<think>.*?</think>", re.S)
TOOL_BLOCK_RE = re.compile(r"<tool_call>\s*(.*?)\s*(?:</tool_call>|$)", re.S)
FENCE_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.S)


# ---------------------------------------------------------------------------
# Reading tool calls out of a reply
# ---------------------------------------------------------------------------

@dataclass
class Call:
    name: str
    args: dict[str, Any]
    id: str = ""


def _json_objects(text: str) -> list[dict[str, Any]]:
    """Every top-level {...} in the text that parses as JSON (brace matching, string-aware)."""
    out, depth, start, in_str, esc = [], 0, None, False, False
    for i, ch in enumerate(text):
        if in_str:
            esc = (ch == "\\") and not esc
            if ch == '"' and not esc:
                in_str = False
            elif ch != "\\":
                esc = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}" and depth:
            depth -= 1
            if depth == 0 and start is not None:
                try:
                    obj = json.loads(text[start:i + 1])
                    if isinstance(obj, dict):
                        out.append(obj)
                except ValueError:
                    pass
                start = None
    return out


def _as_call(obj: dict[str, Any]) -> Call | None:
    name = obj.get("name") or (obj.get("function") or {}).get("name")
    args = obj.get("arguments", obj.get("parameters", obj.get("args", (obj.get("function") or {}).get("arguments"))))
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except ValueError:
            args = None
    if isinstance(name, str) and (args is None or isinstance(args, dict)):
        return Call(name.strip(), args or {})
    return None


def parse_calls(msg: AIMessage) -> tuple[list[Call], str]:
    """(tool calls, the visible text). Structured tool_calls first; then <tool_call> blocks; then JSON in fences."""
    text = THINK_RE.sub("", msg.content if isinstance(msg.content, str) else "")
    if "</think>" in text:
        text = text.split("</think>", 1)[1]
    calls = [Call(tc["name"], dict(tc.get("args") or {}), tc.get("id") or "") for tc in (msg.tool_calls or [])]
    if not calls:
        for block in TOOL_BLOCK_RE.findall(text):
            calls += [c for c in map(_as_call, _json_objects(block)) if c]
        if not calls:
            for block in FENCE_RE.findall(text):
                calls += [c for c in map(_as_call, _json_objects(block)) if c]
        if not calls and text.strip().startswith("{"):
            calls += [c for c in map(_as_call, _json_objects(text)) if c]
    visible = TOOL_BLOCK_RE.sub("", text)
    visible = FENCE_RE.sub("", visible).strip()
    return calls, visible


def compact(value: Any, limit: int = 1400) -> str:
    """A tool result as compact JSON, cut to `limit` characters (a 3B model has a short attention span)."""
    text = json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)
    return text if len(text) <= limit else text[:limit] + '..."(truncated)"'


# ---------------------------------------------------------------------------
# The loop
# ---------------------------------------------------------------------------

class LoopState(TypedDict, total=False):
    messages: list[BaseMessage]
    pending: list[dict[str, Any]]
    calls: list[dict[str, Any]]
    turns: list[dict[str, Any]]
    errors: int
    streak: int
    final: dict[str, Any] | None
    stop: str | None


@dataclass
class LoopResult:
    final: dict[str, Any] | None
    stop: str
    calls: list[dict[str, Any]]
    turns: list[dict[str, Any]]
    errors: int
    protocol: str
    ms: int = 0
    model: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def summary(self) -> dict[str, Any]:
        return {"stop": self.stop, "calls": len(self.calls), "turns": len(self.turns), "errors": self.errors,
                "protocol": self.protocol, "ms": self.ms, "model": self.model}


class AgentLoop:
    def __init__(self, model: Model, name: str, system: str, tools: list[BaseTool], finish: BaseTool | None,
                 max_calls: int, max_turns: int, emit: Emit | None = None, max_tokens: int = 420,
                 parallel: int = 2, tool_timeout: float = 25.0):
        self.model, self.name, self.system = model, name, system
        self.tools = {t.name: t for t in tools}
        self.finish = finish
        self.max_calls, self.max_turns, self.max_tokens = max_calls, max_turns, max_tokens
        self.parallel, self.tool_timeout = parallel, tool_timeout
        self.emit = emit or _noop
        self.graph = self._build().compile()

    @property
    def offered(self) -> list[BaseTool]:
        return list(self.tools.values()) + ([self.finish] if self.finish else [])

    # -- nodes ------------------------------------------------------------------

    async def think(self, s: LoopState) -> dict[str, Any]:
        calls, turns = s.get("calls", []), s.get("turns", [])
        last_turn = len(turns) + 1 >= self.max_turns or len(calls) >= self.max_calls
        messages = list(s["messages"])
        offered = self.offered
        if last_turn and self.finish:
            messages.append(HumanMessage(content=f"You have used your budget ({len(calls)} tool calls). "
                                                 f"Call {self.finish.name} now with what the results show."))
            offered = [self.finish]
        await self.emit({"type": "agent.turn", "loop": self.name, "turn": len(turns) + 1, "state": "started"})
        try:
            msg, gen = await self.model.think(messages, offered, self.max_tokens)
        except Exception as exc:
            logger.warning("%s: model turn failed: %s", self.name, exc)
            return {"stop": f"model error: {str(exc)[:160]}", "pending": []}
        found, visible = parse_calls(msg)
        turn = {"turn": len(turns) + 1, "thought": visible[:600], "requested": [{"name": c.name, "args": c.args} for c in found],
                "ms": gen.ms, "tokens": gen.completion_tokens, "lastTurn": last_turn}
        await self.emit({"type": "agent.turn", "loop": self.name, "state": "finished", **turn})
        update: dict[str, Any] = {"turns": turns + [turn]}

        fin = next((c for c in found if self.finish and c.name == self.finish.name), None)
        if fin:
            try:
                args = self.finish.args_schema.model_validate(fin.args).model_dump() if self.finish.args_schema else fin.args
            except ValidationError as exc:
                if not last_turn and s.get("streak", 0) < 2:
                    return {**update, "pending": [], "errors": s.get("errors", 0) + 1, "streak": s.get("streak", 0) + 1,
                            "messages": messages + [self._assistant(msg, found), HumanMessage(
                                content=f"{self.finish.name} arguments are invalid: {_short(exc)}. Call it again correctly.")]}
                args = {"_invalid": _short(exc), **fin.args}
            return {**update, "final": args, "stop": "finished", "pending": []}
        if found and not last_turn:
            return {**update, "pending": [{"name": c.name, "args": c.args, "id": c.id} for c in found[:self.parallel]],
                    "messages": messages}
        if not self.finish:                              # an answering loop: plain text is the answer
            return {**update, "final": {"text": visible}, "stop": "answered", "pending": []}
        streak = s.get("streak", 0) + 1
        if streak >= 3 or last_turn:
            return {**update, "stop": "no usable tool call" if not last_turn else "budget used up", "pending": [],
                    "errors": s.get("errors", 0) + 1, "streak": streak}
        nudge = (f"Reply with a tool call. Use one of: {', '.join(self.tools)}; "
                 f"or call {self.finish.name} when you are done.")
        return {**update, "pending": [], "errors": s.get("errors", 0) + 1, "streak": streak,
                "messages": messages + [AIMessage(content=visible or "(no tool call)"), HumanMessage(content=nudge)]}

    def _assistant(self, msg: AIMessage, calls: list[Call]) -> AIMessage:
        if self.model.protocol == "native":
            return AIMessage(content=THINK_RE.sub("", msg.content if isinstance(msg.content, str) else "").strip(),
                             tool_calls=[{"id": c.id, "name": c.name, "args": c.args} for c in calls])
        return AIMessage(content=msg.content if isinstance(msg.content, str) else "")

    async def act(self, s: LoopState) -> dict[str, Any]:
        calls, messages = list(s.get("calls", [])), list(s["messages"])
        errors, streak = s.get("errors", 0), s.get("streak", 0)
        done = {(c["name"], json.dumps(c["args"], sort_keys=True)) for c in calls if c["ok"]}
        results: list[tuple[Call, str]] = []
        any_ok = False
        for p in s["pending"]:
            cid = f"c{len(calls) + 1}"
            call = Call(p["name"], p["args"], p.get("id") or cid)
            rec: dict[str, Any] = {"id": cid, "name": call.name, "args": call.args, "ok": False, "ms": 0}
            t0 = time.monotonic()
            tool = self.tools.get(call.name)
            if tool is None:
                obs = {"error": f"There is no tool named {call.name}. Tools: {', '.join(self.tools)}."}
            elif len(calls) >= self.max_calls:
                obs = {"error": "Tool budget used up."}
            else:
                try:
                    args = tool.args_schema.model_validate(call.args).model_dump() if tool.args_schema else call.args
                except ValidationError as exc:
                    args, obs = None, {"error": f"Invalid arguments for {call.name}: {_short(exc)}"}
                if args is not None:
                    key = (call.name, json.dumps(args, sort_keys=True))
                    if key in done:
                        obs = {"error": f"You already called {call.name} with these arguments; the result is above. "
                                        "Try something else or finish."}
                    else:
                        try:
                            obs = await asyncio.wait_for(tool.ainvoke(args), self.tool_timeout)
                            rec.update(ok=True, args=args)
                            done.add(key)
                            any_ok = True
                        except Exception as exc:
                            obs = {"error": f"{call.name} failed: {str(exc)[:200]}"}
            rec["ms"] = int((time.monotonic() - t0) * 1000)
            rec["observation"] = obs
            if not rec["ok"]:
                errors += 1
            calls.append(rec)
            results.append((call, compact({"call": cid, "result": obs})))
            await self.emit({"type": "agent.call", "loop": self.name, "call": {k: rec[k] for k in ("id", "name", "args", "ok", "ms")},
                             "observation": compact(obs, 600)})
        streak = 0 if any_ok else streak + 1
        if self.model.protocol == "native":
            messages.append(AIMessage(content="", tool_calls=[{"id": c.id, "name": c.name, "args": c.args} for c, _ in results]))
            messages += [ToolMessage(content=r, tool_call_id=c.id) for c, r in results]
        else:
            messages.append(AIMessage(content="\n".join(
                f'<tool_call>\n{json.dumps({"name": c.name, "arguments": c.args})}\n</tool_call>' for c, _ in results)))
            messages.append(HumanMessage(content="\n".join(f"<tool_response>\n{r}\n</tool_response>" for _, r in results)))
        out: dict[str, Any] = {"calls": calls, "messages": messages, "pending": [], "errors": errors, "streak": streak}
        if streak >= 3:
            out["stop"] = "three invalid turns in a row"
        return out

    def _build(self) -> StateGraph:
        g = StateGraph(LoopState)
        g.add_node("think", self.think)
        g.add_node("act", self.act)
        g.add_edge(START, "think")
        g.add_conditional_edges("think", lambda s: "act" if s.get("pending") and not s.get("stop") else
                                ("end" if s.get("stop") else "think"), {"act": "act", "think": "think", "end": END})
        g.add_conditional_edges("act", lambda s: "end" if s.get("stop") else "think", {"think": "think", "end": END})
        return g

    async def run(self, brief: str) -> LoopResult:
        t0 = time.monotonic()
        state = await self.graph.ainvoke(
            {"messages": [SystemMessage(content=self.system), HumanMessage(content=brief)], "calls": [], "turns": [],
             "errors": 0, "streak": 0, "final": None, "stop": None, "pending": []},
            {"recursion_limit": 4 * (self.max_turns + self.max_calls) + 8})
        return LoopResult(state.get("final"), state.get("stop") or "ended", state.get("calls", []), state.get("turns", []),
                          state.get("errors", 0), self.model.protocol, int((time.monotonic() - t0) * 1000), self.model.model)

    def describe(self) -> dict[str, Any]:
        drawn = self.graph.get_graph()
        return {"name": self.name, "nodes": [n for n in drawn.nodes if not n.startswith("__")],
                "edges": [{"from": e.source, "to": e.target, "conditional": e.conditional} for e in drawn.edges],
                "mermaid": drawn.draw_mermaid(), "tools": [t.name for t in self.offered],
                "maxCalls": self.max_calls, "maxTurns": self.max_turns}


def _short(exc: ValidationError) -> str:
    try:
        return "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors()[:3])
    except Exception:
        return str(exc)[:200]


async def _noop(_event: dict[str, Any]) -> None:
    return None
