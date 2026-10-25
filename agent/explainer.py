"""
Explanations: the only thing the language model does.

The validator's checks and verdict are deterministic rules (checks.py). A
small local model served by oMLX turns those results into prose an officer,
or the citizen, can read. The model never decides anything, and nothing it
writes is used until the hallucination measurer (quality.py) has passed it:

  draft    one streamed completion through LangChain (llm.py), from a prompt
           built out of sanitised facts (prompts.py, guard.py)
  measure  quality.measure() scores the text against the same facts
  retry    a failed text is redrafted once, with the checker's complaints in
           the prompt (the graph in graph.py decides; this file only writes)
  template the deterministic writer below, used when the model is offline or
           its text keeps failing; it is itself run through the measurer in
           tests, and scores 1.0

The report records which writer produced each text and why.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from langchain_core.messages import BaseMessage

from . import guard, prompts, settings
from .llm import Generation, Model, TokenFn

KNOWLEDGE = Path(__file__).parent / "knowledge" / "architecture.md"


# ---------------------------------------------------------------------------
# Architecture knowledge (sections of knowledge/architecture.md)
# ---------------------------------------------------------------------------

def knowledge_sections() -> dict[str, str]:
    text = KNOWLEDGE.read_text()
    sections: dict[str, str] = {}
    for block in re.split(r"^## ", text, flags=re.M)[1:]:
        title, _, body = block.partition("\n")
        sections[title.strip()] = body.strip()
    return sections


def select_sections(question: str, k: int = 3, always: tuple[str, ...] = ()) -> list[tuple[str, str]]:
    """Tiny retrieval: rank sections by word overlap with the question."""
    words = {w for w in re.findall(r"[a-z0-9]+", question.lower()) if len(w) > 3}
    secs = knowledge_sections()
    scored = sorted(secs.items(), key=lambda kv: -len(words & set(re.findall(r"[a-z0-9]+", (kv[0] + " " + kv[1]).lower()))))
    chosen = [s for s in scored if s[0] in always] + [s for s in scored if s[0] not in always][:k]
    return chosen


# ---------------------------------------------------------------------------
# Template writer (deterministic fallback)
# ---------------------------------------------------------------------------

def _clause(c: dict[str, Any]) -> str:
    return f"{c['label'].rstrip('.')} ({c['detail'].rstrip('.')})"


def template_explanation(facts: dict[str, Any], audience: str) -> str:
    checks = facts["checks"]
    fails = [c for c in checks if c["status"] == "fail"]
    warns = [c for c in checks if c["status"] == "warn"]
    ran = [c for c in checks if c["status"] != "skipped"]
    status = facts["verdict"]["status"]
    if audience == "citizen":
        intro = f"We automatically checked the transfer of parcel {facts['surveyNo']}."
        nxt = "A Revenue officer reviews this next, and the Collector makes the final decision."
        if status == "clear":
            return f"{intro} Your documents match what was registered and everything we checked is in order. {nxt}"
        items = ", ".join(c["label"].lower() for c in fails + warns)
        if status == "flagged":
            return (f"{intro} We found something an officer must look into: {items}. "
                    f"Nothing has been decided yet. {nxt}")
        return (f"{intro} A few items need an officer to take a closer look: {items}. "
                f"This is not a rejection. {nxt}")
    parts = [f"{len(ran)} checks ran on application {facts['applicationId']} for parcel {facts['surveyNo']}."]
    if fails:
        parts.append(f"{len(fails)} failed: " + "; ".join(_clause(c) for c in fails) + ".")
    if warns:
        parts.append(f"{len(warns)} need attention: " + "; ".join(_clause(c) for c in warns) + ".")
    if not fails and not warns:
        parts.append("Every check passed: the documents match their fingerprints on the ledger, the deed describes "
                     "this parcel and these parties, and both parties' consents are signed with their registered DIDs.")
    parts.append(f"The verdict '{status}' follows the fixed rule (flagged if any check fails, attention if any warns). "
                 "This report is evidence for the Revenue verifier and the Collector; the decision is the Collector's.")
    return " ".join(parts)


# ---------------------------------------------------------------------------
# The language-model writer
# ---------------------------------------------------------------------------

class Explainer(Model):
    def prompt(self, facts: dict[str, Any], audience: str, feedback: str | None = None) -> list[BaseMessage]:
        """The messages for one draft. Facts are sanitised first: a document may try to talk to the model."""
        safe = guard.sanitise(facts)
        retry = prompts.RETRY.format(feedback=feedback) if feedback else ""
        return prompts.EXPLAIN.format_messages(
            system=prompts.SYSTEM, facts=prompts.render_facts(safe), retry=retry,
            task=prompts.CITIZEN_TASK if audience == "citizen" else prompts.OFFICER_TASK)

    async def draft(self, facts: dict[str, Any], audience: str, on_token: TokenFn | None = None,
                    feedback: str | None = None) -> Generation:
        return await self.chat(self.prompt(facts, audience, feedback), 380 if audience == "officer" else 220, on_token)
