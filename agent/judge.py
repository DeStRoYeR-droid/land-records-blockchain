"""
An LLM judge: a second opinion, never a gate.

The deterministic measurer (quality.py) decides whether a text is used. This
asks the same small model one extra question -- "which numbered sentences
state something the facts do not support?" -- and records the answer next to
the measurer's. Where the two disagree, the dashboard shows it. A 3B model is
too unreliable to be allowed to pass or fail a text, but its disagreement is
worth a human glance, and agreement over many runs is a useful signal about
both.

One call, JSON output, parsed defensively.
"""

from __future__ import annotations

import json
import re
from typing import Any

from . import guard, prompts, settings
from .llm import Model


def parse(raw: str, n: int) -> list[int] | None:
    """Pull {"unsupported": [..]} out of whatever the model wrote. None if it is not usable."""
    m = re.search(r"\{.*?\}", raw, re.S)
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
        vals = data["unsupported"]
        out = sorted({int(x) for x in vals if 1 <= int(x) <= n})
        return out
    except (ValueError, KeyError, TypeError):
        return None


def wanted(model: Model) -> bool:
    return settings.JUDGE_MODE != "off" and model.engine == "omlx"


async def judge(model: Model, facts: dict[str, Any], text: str, measured: dict[str, Any]) -> dict[str, Any]:
    sents = [s["text"] for s in measured["sentences"]]
    if not sents:
        return {"ran": False, "reason": "no sentences"}
    # the facts again, but only the compact form; the judge prompt must stay short
    body = prompts.render_facts(guard.sanitise(facts))
    numbered = "\n".join(f"{i}. {s}" for i, s in enumerate(sents, 1))
    try:
        gen = await model.chat(prompts.JUDGE.format_messages(facts=body, sentences=numbered), 60, None, temperature=0.0)
    except Exception as exc:
        return {"ran": False, "reason": f"judge call failed: {str(exc)[:120]}"}
    flagged = parse(gen.text, len(sents))
    if flagged is None:
        return {"ran": True, "usable": False, "raw": gen.text[:120], **gen.meta()}
    weak = sorted(i for i, x in enumerate(measured["sentences"], 1) if x["weak"])
    measurer_objects = bool(measured["hardViolations"] or weak)
    return {"ran": True, "usable": True, "unsupportedSentences": flagged, "measurerWeakSentences": weak,
            "agrees": bool(flagged) == measurer_objects, **gen.meta()}
