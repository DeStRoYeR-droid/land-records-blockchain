"""
The language model, reached through LangChain.

oMLX (https://github.com/jundot/omlx) serves MLX models on Apple Silicon and
speaks the OpenAI chat API, so LangChain's `ChatOpenAI` talks to it by
pointing `base_url` at it. This module owns:

  probe()    is oMLX up, is the key accepted, which model will be used
  status()   what the dashboards show
  chat()     one streamed completion, with <think> removal and speed numbers
  think()    one tool-calling turn for the agent loops (agentic.py): the model
             either asks for tools or answers. oMLX parses tool calls itself
             for Qwen-family models; if a server cannot take `tools`, the
             same turn is sent with the tools described in the prompt and the
             <tool_call> blocks are parsed here instead

Nothing here decides anything: the language model only writes sentences, and
the graph checks every sentence before it is used.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

import httpx
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_core.tools import BaseTool
from langchain_openai import ChatOpenAI

from nodes.common import log

from . import settings

logger = log.get("llm")
TokenFn = Callable[[str], Awaitable[None]]
THINKING_MODELS = re.compile(r"qwen3|qwq|deepseek-r1|r1-distill|thinking|magistral|gpt-oss", re.I)


@dataclass
class Generation:
    text: str
    ms: int
    completion_tokens: int
    prompt_tokens: int | None
    estimated: bool                 # token counts guessed from the text (server sent no usage)
    first_token_ms: int | None = None

    @property
    def tokens_per_second(self) -> float | None:
        gen_ms = self.ms - (self.first_token_ms or 0)
        return round(self.completion_tokens / (gen_ms / 1000), 1) if gen_ms > 50 and self.completion_tokens else None

    def meta(self) -> dict[str, Any]:
        return {"ms": self.ms, "completionTokens": self.completion_tokens, "promptTokens": self.prompt_tokens,
                "tokensPerSecond": self.tokens_per_second, "firstTokenMs": self.first_token_ms,
                "tokenCountEstimated": self.estimated}


class ThinkFilter:
    """Remove <think>...</think> from a token stream without ever un-emitting text."""

    def __init__(self, hold_until_close: bool):
        self.raw = ""
        self.emitted = 0
        self.hold = hold_until_close  # implicit-think models: hold until </think> appears

    def visible(self, final: bool = False) -> str:
        text = re.sub(r"<think>.*?</think>", "", self.raw, flags=re.S)          # complete blocks
        if "</think>" in text:                                                    # opener was in the prompt template
            text = text.split("</think>", 1)[1]
        elif "<think>" in text:                                                   # unclosed block
            text = text.split("<think>", 1)[0]
        elif self.hold and not final and "</think>" not in self.raw:              # thinks without an opening tag
            return ""
        if not final:                                                             # never emit a partial "<thi"
            for tag in ("<think>", "</think>"):
                for i in range(len(tag) - 1, 0, -1):
                    if text.endswith(tag[:i]):
                        text = text[:-i]
                        break
        return text.lstrip()

    def feed(self, chunk: str) -> str:
        self.raw += chunk
        vis = self.visible()
        delta = vis[self.emitted:] if len(vis) > self.emitted else ""
        self.emitted = max(self.emitted, len(vis))
        return delta

    def finish(self) -> tuple[str, str]:
        vis = self.visible(final=True)
        delta = vis[self.emitted:] if len(vis) > self.emitted else ""
        return vis.strip(), delta


class Model:
    def __init__(self) -> None:
        self.base = settings.OMLX_BASE_URL
        self.mode = settings.EXPLAINER_MODE
        self.model: str | None = settings.OMLX_MODEL or None
        self.models: list[str] = []
        self.reachable = False
        self.last_error: str | None = None
        self.last_latency_ms: int | None = None
        self.last_probe = 0.0
        self.http = httpx.AsyncClient(timeout=httpx.Timeout(settings.OMLX_TIMEOUT, connect=2.0))
        self.usage_ok = settings.OMLX_STREAM_USAGE != "0"
        self.recent: list[dict[str, Any]] = []        # speed of the last generations, for the dashboard
        self.tool_protocol = settings.AGENT_TOOL_PROTOCOL  # auto | native | text
        self.native_refused: str | None = None         # why the server refused `tools`, if it did

    # -- availability -----------------------------------------------------------

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {settings.OMLX_API_KEY}"} if settings.OMLX_API_KEY else {}

    async def probe(self, force: bool = False) -> None:
        if self.mode == "template":
            self.reachable = False
            return
        if not force and time.monotonic() - self.last_probe < 20:
            return
        self.last_probe = time.monotonic()
        try:
            resp = await self.http.get(f"{self.base}/models", headers=self._headers(), timeout=4)
            if resp.status_code == 401:
                raise RuntimeError("oMLX rejected the API key (set OMLX_API_KEY in landchain.env)")
            resp.raise_for_status()
            self.models = [m["id"] for m in resp.json().get("data", [])]
            if not self.models:
                raise RuntimeError("oMLX is running but no model is available")
            if not self.model or self.model not in self.models:
                if settings.OMLX_MODEL and settings.OMLX_MODEL not in self.models:
                    logger.warning("OMLX_MODEL=%s not served; using %s", settings.OMLX_MODEL, self.models[0])
                self.model = self.models[0]
            if not self.reachable:
                logger.info("oMLX reachable at %s, model %s", self.base, self.model)
            self.reachable, self.last_error = True, None
        except Exception as exc:
            if self.reachable or self.last_error is None:
                logger.info("oMLX not reachable at %s (%s); using the template writer", self.base, exc)
            self.reachable, self.last_error = False, str(exc)

    @property
    def engine(self) -> str:
        return "omlx" if (self.reachable and self.mode != "template") else "template"

    def status(self) -> dict[str, Any]:
        speeds = [r["tokensPerSecond"] for r in self.recent if r.get("tokensPerSecond")]
        return {"engine": self.engine, "mode": self.mode, "reachable": self.reachable, "baseUrl": self.base,
                "model": self.model, "models": self.models[:12], "lastError": self.last_error,
                "lastLatencyMs": self.last_latency_ms, "framework": "langchain-openai (ChatOpenAI)",
                "apiKeyConfigured": bool(settings.OMLX_API_KEY),    # whether, never the key itself
                "avgTokensPerSecond": round(sum(speeds) / len(speeds), 1) if speeds else None,
                "toolProtocol": self.protocol, "nativeToolsRefused": self.native_refused}

    @property
    def protocol(self) -> str:
        """How tool calls travel: 'native' (OpenAI `tools`, parsed by oMLX) or 'text' (<tool_call> blocks we parse)."""
        if self.tool_protocol in ("native", "text"):
            return self.tool_protocol
        return "text" if self.native_refused else "native"

    async def think(self, messages: list[BaseMessage], tools: list[BaseTool], max_tokens: int = 400,
                    temperature: float = 0.1) -> tuple[AIMessage, Generation]:
        """One non-streamed turn with tools available. Returns the raw reply and its timing."""
        assert self.model, "probe() first"
        llm = ChatOpenAI(model=self.model, base_url=self.base, api_key=settings.OMLX_API_KEY or "not-needed",
                         max_tokens=max_tokens, temperature=temperature, max_retries=0, http_async_client=self.http)
        start = time.monotonic()
        if self.protocol == "native":
            try:
                msg = await llm.bind_tools(tools).ainvoke(messages)
            except Exception as exc:
                if self.tool_protocol == "auto" and re.search(r"tool|400|422|unsupported|unrecognized|extra", str(exc), re.I):
                    logger.info("the server refused `tools` (%s); describing tools in the prompt instead", str(exc)[:120])
                    self.native_refused = str(exc)[:200]
                    return await self.think(messages, tools, max_tokens, temperature)
                raise
        else:
            msg = await llm.ainvoke(with_tool_text(messages, tools))
        ms = int((time.monotonic() - start) * 1000)
        self.last_latency_ms = ms
        usage = getattr(msg, "usage_metadata", None) or {}
        text = msg.content if isinstance(msg.content, str) else ""
        out_tokens = usage.get("output_tokens")
        gen = Generation(text, ms, int(out_tokens or max(1, round(len(text) / 4))), usage.get("input_tokens"),
                         not out_tokens, None)
        self.recent = (self.recent + [gen.meta()])[-20:]
        return msg, gen

    # -- one completion -------------------------------------------------------------

    def _chat_model(self, max_tokens: int, temperature: float, usage: bool) -> ChatOpenAI:
        return ChatOpenAI(model=self.model, base_url=self.base, api_key=settings.OMLX_API_KEY or "not-needed",
                          max_tokens=max_tokens, temperature=temperature, streaming=True, stream_usage=usage,
                          max_retries=0, http_async_client=self.http, http_socket_options=())

    async def chat(self, messages: list[BaseMessage], max_tokens: int, on_token: TokenFn | None = None,
                   temperature: float | None = None) -> Generation:
        assert self.model, "probe() first"
        no_think = settings.OMLX_NO_THINK == "1" or (settings.OMLX_NO_THINK == "auto"
                                                      and bool(THINKING_MODELS.search(self.model)))
        if no_think and messages and isinstance(messages[-1], HumanMessage):
            messages = messages[:-1] + [HumanMessage(content=f"{messages[-1].content}\n/no_think")]
        temp = settings.OMLX_TEMPERATURE if temperature is None else temperature
        for attempt in (0, 1):
            filt = ThinkFilter(hold_until_close=bool(THINKING_MODELS.search(self.model)) and not no_think)
            start = time.monotonic()
            first: int | None = None
            pieces, usage = 0, None
            try:
                async for chunk in self._chat_model(max_tokens, temp, self.usage_ok).astream(messages):
                    if chunk.usage_metadata:
                        usage = chunk.usage_metadata
                    piece = chunk.content if isinstance(chunk.content, str) else ""
                    if piece:
                        if first is None:
                            first = int((time.monotonic() - start) * 1000)
                        pieces += 1
                        delta = filt.feed(piece)
                        if delta and on_token:
                            await on_token(delta)
                break
            except Exception as exc:
                refused_usage = self.usage_ok and first is None and attempt == 0 and \
                    re.search(r"stream_options|include_usage|unrecognized|unexpected keyword", str(exc), re.I)
                if refused_usage:
                    logger.info("oMLX does not accept stream usage; token counts will be estimated")
                    self.usage_ok = False
                    continue
                raise
        text, delta = filt.finish()
        if delta and on_token:
            await on_token(delta)
        ms = int((time.monotonic() - start) * 1000)
        self.last_latency_ms = ms
        out_tokens = (usage or {}).get("output_tokens")
        estimated = not out_tokens
        gen = Generation(text, ms, int(out_tokens or max(pieces, round(len(filt.raw) / 4))),
                         (usage or {}).get("input_tokens"), estimated, first)
        self.recent = (self.recent + [gen.meta()])[-20:]
        return gen


def tool_signature(tool: BaseTool) -> str:
    props = (tool.args_schema.model_json_schema() if tool.args_schema else {}).get("properties", {})
    args = ", ".join(f"{k}: {v.get('type', 'object')}" for k, v in props.items())
    return f"- {tool.name}({args}): {tool.description}"


def with_tool_text(messages: list[BaseMessage], tools: list[BaseTool]) -> list[BaseMessage]:
    """The text protocol: tools described in the system prompt, calls as Qwen's own <tool_call> blocks."""
    block = ("\n\nTOOLS. To call one, reply with exactly one block per call:\n<tool_call>\n"
             '{"name": "<tool>", "arguments": {...}}\n</tool_call>\nAvailable tools:\n'
             + "\n".join(tool_signature(t) for t in tools))
    if messages and isinstance(messages[0], SystemMessage):
        return [SystemMessage(content=f"{messages[0].content}{block}")] + list(messages[1:])
    return [SystemMessage(content=block.strip())] + list(messages)

