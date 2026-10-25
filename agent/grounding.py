"""
Grounding the investigator's findings: deterministic, and shared with the independent verifier.

A finding is accepted only if
  * it cites at least one tool call that was made and succeeded,
  * every number, identifier, date and name in it appears in the results of
    the calls it cites (or in the brief the model was given),
  * every small count ("3 other files") is a count those results contain,
  * it does not recommend or state a decision.
The verifier imports this module (and the measurer), never the agent loop.
"""

from __future__ import annotations

import re
from typing import Any

from . import quality

# A finding may describe approved or rejected files ("4 approved sales nearby"); it may not decide or advise.
DECIDES = re.compile(r"\b(recommend\w*|advis\w+|should (?:be )?(?:approved|rejected|approve|reject|denied|blocked)|"
                     r"must (?:be )?(?:approved|rejected|denied|blocked)|(?:approve|reject|deny|grant|sanction|block) "
                     r"(?:this|the|it)\b|is safe to approve|can be approved)", re.I)

def ground(finding: dict[str, Any], calls: dict[str, dict[str, Any]], facts: dict[str, Any]) -> list[str]:
    """Why a finding cannot be accepted ([] = grounded). Deterministic; the verifier runs it again."""
    reasons = []
    claim = str(finding.get("claim") or "").strip()
    cited = [c for c in finding.get("evidence") or [] if isinstance(c, str)]
    if len(claim) < 12:
        reasons.append("the claim is empty or too short")
    if len(claim) > 400:
        reasons.append("the claim is too long")
    if not cited:
        reasons.append("it cites no tool call")
    missing = [c for c in cited if c not in calls]
    if missing:
        reasons.append(f"it cites calls that were not made: {', '.join(missing[:3])}")
    good = [calls[c] for c in cited if c in calls and calls[c].get("ok")]
    if cited and not good and not missing:
        reasons.append("the calls it cites returned errors")
    idx = quality.FactIndex({"observations": [g.get("observation") for g in good], "brief": facts,
                             "checks": []})
    claims, _ = quality._entities(claim, idx)  # noqa: SLF001 -- the measurer's own entity check
    bad = [c["text"] for c in claims if not c["supported"]]
    if bad:
        reasons.append("not in the cited results: " + ", ".join(bad[:4]))
    small = {int(m) for m in re.findall(r"(?<![\w.,/-])(\d{1,2})(?![\w.,%/-])", claim)}
    known = set(_ints([g.get("observation") for g in good], facts))
    unknown_counts = sorted(n for n in small if n > 1 and n not in known)
    if unknown_counts:
        reasons.append("counts not in the cited results: " + ", ".join(map(str, unknown_counts[:4])))
    if DECIDES.search(claim):
        reasons.append("it recommends or states a decision")
    return reasons


def _ints(*values: Any) -> list[int]:
    out: list[int] = []
    for v in values:
        if isinstance(v, bool):
            continue
        if isinstance(v, int):
            out.append(v)
        elif isinstance(v, float) and v.is_integer():
            out.append(int(v))
        elif isinstance(v, dict):
            out += _ints(*v.values())
        elif isinstance(v, list):
            out.append(len(v))
            out += _ints(*v)
    return out


