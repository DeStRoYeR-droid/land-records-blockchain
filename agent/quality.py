"""
The hallucination measurer.

A small local model can write fluent text that is not true. This module takes
a piece of generated text and the facts it was allowed to use and measures,
without calling any model, how faithful the text is. Every number it reports
can be recomputed by anyone from the text and the facts, which is exactly
what the independent verifier does after the report is signed.

What is measured
----------------
entities       numbers, identifiers (APP-, SY-, DOC-, hashes, CIDs), dates and
               proper names in the text. Each must appear in the facts.
decision       any sentence that approves, rejects, grants or recommends
               something without attributing the decision to the Collector.
consistency    the verdict the text states (or implies) must be the verdict
               the rule produced; counts ("3 failed") must match the facts;
               "everything is in order" is a contradiction when a check failed.
coverage       every failed or warning check must be mentioned (omission is a
               kind of untruth). For the citizen: the text must signal that an
               officer needs to look, whenever the verdict is not clear.
support        a softer, per-sentence measure: how many of a sentence's content
               words appear in the facts or in the system's own vocabulary, and
               which checks the sentence rests on (shown as attributions).

The scores
----------
entity, sentenceSupport, consistency, coverage each lie in 0..1.
faithfulness is their weighted mean, halved for every hard violation
(invented entity, unattributed decision, wrong verdict). It is the number to
look at. hallucinationRate is failed claims / all claims checked.

`passed` is the gate used by the agent: no hard violation and
faithfulness >= the threshold. Sentence support alone never fails a text
(plain paraphrase uses words that are in neither list); it only lowers the
score and marks the sentence for a human to look at.

An optional LLM judge (judge.py) gives a second opinion on the same text.
It is recorded, never trusted to gate: a 3B model is a poor judge.
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

VERSION = "landchain.hallucination-measure/v1"
KNOWLEDGE = Path(__file__).parent / "knowledge" / "architecture.md"

ID_PATTERNS = [r"\bAPP-[A-Za-z0-9-]+", r"\bSY-[A-Za-z0-9/.-]+", r"\bDOC-[A-Za-z0-9]+", r"\bbafk[a-z2-7]{10,}",
               r"\b[0-9a-f]{16,64}\b", r"\bDL-[0-9a-f]+", r"\bVR-[A-Za-z0-9-]+"]
MONTHS = "jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec"
DATE_PATTERNS = [
    r"\b\d{4}-\d{2}-\d{2}\b",
    r"\b\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4}\b",
    rf"\b\d{{1,2}}(?:st|nd|rd|th)?\s+(?:{MONTHS})[a-z]*\.?,?\s+\d{{4}}\b",
    rf"\b(?:{MONTHS})[a-z]*\.?\s+\d{{1,2}}(?:st|nd|rd|th)?,?\s+\d{{4}}\b",
]
NUMBER = re.compile(r"(?<![\w./-])\d[\d,]*(?:\.\d+)?%?")
DECISION_WORDS = re.compile(
    r"\b(approv\w*|reject\w*|den(?:y|ied|ies)|grant(?:ed|s)?|sanction\w*|recommend\w*|"
    r"should be (?:approved|rejected|blocked|stopped|cancelled)|must be (?:approved|rejected|blocked))\b", re.I)
NOT_A_DECISION = re.compile(r"\b(?:is |was )?not (?:a |an |the )?(?:rejection|approval|denial|decision)\b|"
                            r"\bno (?:rejection|approval|decision) (?:has|have|was|is)\b", re.I)
ATTRIBUTION = re.compile(r"\b(collector|collectorate|decide[sd]?|decision|decides|officers?)\b", re.I)
ALL_CLEAR = re.compile(
    r"\b(all|every) (?:the |of the )?(?:checks?|items?) (?:passed|(?:are|is|were) in order|came back (?:clean|clear))"
    r"|\bno (?:issues?|problems?|concerns?|irregularit\w+) (?:were |was )?(?:found|detected|seen|identified)"
    r"|\beverything (?:we checked )?(?:is|was|looks?) (?:in order|fine|clear|good)"
    r"|\bnothing (?:is |was )?(?:wrong|amiss|suspicious)", re.I)
FAILURE_WORDS = re.compile(r"\b(fail(?:ed|ure|ures)?|tamper\w*|forg\w+|fraud\w*|suspicious|mismatch\w*)\b", re.I)
NEGATION_NEAR = re.compile(r"\b(no|not|none|without|if any|unless|never)\b", re.I)
VERDICT_WORD = re.compile(r"\bverdict(?: is| was| of| came back as| status is|:)?\s*(?:as )?['\"“]?(clear|attention|flagged)\b", re.I)
COUNT_CLAIM = re.compile(
    r"\b(\d+)\s+(?:of the\s+)?(?:checks?\s+)?(failed|passed|need(?:ed)? attention|warn(?:ed|ings?)|"
    r"could not be run|were skipped|skipped)\b", re.I)
CHECK_COUNT_CLAIM = re.compile(r"\b(\d+)\s+checks?\s+(?:ran|were run|were made|completed)\b", re.I)
SIGNAL_WORDS = re.compile(r"\b(officer|review\w*|look(?:s|ed)? (?:into|closer|at)|attention|found|concern|issue|"
                          r"flag\w*|verif\w+)\b", re.I)

STOPWORDS = set("""a about above after again all also am an and any are as at be because been before being below between
both but by can could did do does doing down during each few for from further had has have having he her here hers him his
how i if in into is it its itself just me more most my no nor not now of off on once only or other our out over own same
she should so some such than that the their them then there these they this those through to too under until up very was we
were what when where which while who whom why will with would you your yours also per via""".split())
# words a fair paraphrase uses that are in no list of facts; kept short on purpose
GENERIC = set("""automatic automatically checked check checks found find finding findings next step steps now still yet
nothing something everything anything items item issue issues point points matter matters looks look looked closer
carefully careful further follow review reviews reviewed reviewing officer officers collector final decision decide
decided decides report reports evidence validator validate validated rule rules rule-based result results summary
transfer transfers sale sold buyer seller land parcel application file filed document documents deed certificate
approved rejected approval ready ahead process processed processing complete completed completing status stage
passed failed failure warning warnings attention flagged clear order matches match matched matching consistent
unusual normal different difference differences expected record records recorded registered registration revenue
department ledger fingerprint fingerprints signature signed anchored trail history owner owners ownership
please note noted note means mean meant based including include includes included needs need needed required require
take taken takes made make makes made given give gives shown show shows showing shows reason reasons detail details
unless however therefore because since although though whether either neither within without already""".split())
PROPER_ALLOWED = {"I", "A", "The", "This", "That", "It", "Rs", "INR", "SHA", "DID", "PGP", "OpenPGP", "CID",
                  "Revenue", "Registration", "Collector", "Collectorate", "LandChain", "DigiLocker", "Aadhaar",
                  "Verdict", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"}


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def stem(word: str) -> str:
    w = word.lower().strip("'’")
    for suf in ("ingly", "edly", "ing", "ies", "ied", "ed", "es", "ly", "s"):
        if len(w) > len(suf) + 3 and w.endswith(suf):
            w = w[: -len(suf)] + ("y" if suf in ("ies", "ied") else "")
            break
    return w


def tokens(text: str) -> list[str]:
    return re.findall(r"[A-Za-z][A-Za-z'’-]*", text)


def content_stems(text: str) -> list[str]:
    return [stem(t) for t in tokens(text) if len(t) > 3 and t.lower() not in STOPWORDS]


def sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z0-9\"“'(])|\n+", text.strip())
    return [p.strip() for p in parts if p and p.strip()]


@lru_cache(maxsize=1)
def system_vocabulary() -> frozenset[str]:
    try:
        text = KNOWLEDGE.read_text()
    except OSError:
        text = ""
    return frozenset({stem(t) for t in tokens(text)} | {stem(t) for t in GENERIC})


@lru_cache(maxsize=1)
def system_proper() -> frozenset[str]:
    try:
        text = KNOWLEDGE.read_text()
    except OSError:
        text = ""
    return frozenset(re.findall(r"\b[A-Z][A-Za-z]+\b", text)) | frozenset(PROPER_ALLOWED)


def _strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [s for v in value.values() for s in _strings(v)]
    if isinstance(value, (list, tuple)):
        return [s for v in value for s in _strings(v)]
    return [str(value)] if isinstance(value, (int, float)) and not isinstance(value, bool) else []


class FactIndex:
    """The facts, indexed for the questions the measurer asks."""

    def __init__(self, facts: dict[str, Any]):
        self.facts = facts
        self.text = json.dumps(facts, ensure_ascii=False)
        self.flat = self.text.replace(",", "")
        self.lower = self.text.lower()
        words = " ".join(_strings(facts))
        self.stems = {stem(t) for t in tokens(words)}
        self.proper = {t for t in re.findall(r"\b[A-Z][A-Za-z]+\b", words)} | {t.lower() for t in re.findall(r"\b[A-Za-z]+\b", words)}
        self.quotable = sorted({s.rstrip(".") for c in facts.get("checks", [])
                                for s in [c.get("label", ""), c.get("detail", "")] + list(c.get("evidence", []))
                                if len(s) > 8}, key=len, reverse=True)
        self.check_stems = {c["id"]: set(content_stems(" ".join([c.get("label", ""), c.get("detail", "")]
                                                                + list(c.get("evidence", [])))))
                            for c in facts.get("checks", [])}
        self.counts = facts.get("counts", {})
        self.verdict = facts.get("verdict", {}).get("status")

    def has_number(self, raw: str) -> bool:
        num = raw.replace(",", "").rstrip("%").rstrip(".")
        return bool(num) and num in self.flat

    def has_id(self, tok: str) -> bool:
        return tok in self.text

    def has_date(self, raw: str) -> bool:
        if raw.lower() in self.lower:
            return True
        digits = re.findall(r"\d+", raw)
        return len(digits) >= 3 and all(d.lstrip("0") in self.flat or d in self.flat for d in digits)


# ---------------------------------------------------------------------------
# measurement
# ---------------------------------------------------------------------------

def _claim(kind: str, text: str, ok: bool, reason: str = "") -> dict[str, Any]:
    return {"kind": kind, "text": text[:120], "supported": ok, "reason": reason}


def _entities(text: str, idx: FactIndex) -> tuple[list[dict[str, Any]], str]:
    claims: list[dict[str, Any]] = []
    work = text
    for pat in DATE_PATTERNS:                                   # dates first, so their digits are not re-counted
        for m in re.finditer(pat, work, re.I):
            claims.append(_claim("date", m.group(0), idx.has_date(m.group(0)), "date not in the facts"))
        work = re.sub(pat, " ", work, flags=re.I)
    for pat in ID_PATTERNS:
        for m in re.findall(pat, work):
            tok = m.rstrip(".,;:)")
            claims.append(_claim("identifier", tok, idx.has_id(tok), "identifier not in the facts"))
        work = re.sub(pat, " ", work)
    for m in NUMBER.findall(work):
        num = m.replace(",", "").rstrip("%").rstrip(".")
        if not num or (num.isdigit() and int(num) <= 20 and not m.endswith("%")):
            continue                                            # small counts ("3 checks") are checked as count claims
        claims.append(_claim("number", m, idx.has_number(m), "number not in the facts"))
    # proper names: capitalised words that are not the first word of a sentence
    for sent in sentences(text):
        words = list(re.finditer(r"[A-Za-z][A-Za-z'’-]*", sent))
        for i, m in enumerate(words):
            w = m.group(0)
            if i == 0 or not w[0].isupper() or len(w) < 3 or w.isupper():   # all-caps: acronyms (DID, SHA)
                continue
            base = re.sub(r"['’]s$", "", w)
            known = system_proper()
            if base in known or (base.endswith("s") and base[:-1] in known):
                continue
            if base.lower() in idx.proper or base in idx.proper or stem(base) in idx.stems:
                continue
            claims.append(_claim("name", w, False, "name not in the facts"))
    return claims, work


def _decision_violations(text: str, idx: FactIndex) -> list[str]:
    residual = text
    for s in idx.quotable:                                      # quoting a check's own words is not a verdict
        residual = residual.replace(s, " ")
    out = []
    for sent in sentences(residual):
        sent_ = NOT_A_DECISION.sub(" ", sent)               # "This is not a rejection." decides nothing
        if DECISION_WORDS.search(sent_) and not ATTRIBUTION.search(sent_):
            out.append(sent.strip()[:100])
    return out


def _consistency(text: str, idx: FactIndex) -> tuple[list[dict[str, Any]], list[str]]:
    claims: list[dict[str, Any]] = []
    problems: list[str] = []
    verdict, counts = idx.verdict, idx.counts
    for m in VERDICT_WORD.finditer(text):
        said = m.group(1).lower()
        ok = said == verdict
        claims.append(_claim("verdict", m.group(0), ok, f"text says '{said}', the rule gave '{verdict}'"))
        if not ok:
            problems.append(f"states verdict '{said}' but the rule gave '{verdict}'")
    key = {"failed": "fail", "passed": "pass", "attention": "warn", "warn": "warn", "could": "skipped", "were": "skipped",
           "skipped": "skipped"}
    for m in COUNT_CLAIM.finditer(text):
        label = m.group(2).lower()
        field = "fail" if label.startswith("fail") else "pass" if label.startswith("pass") else \
            "skipped" if ("skip" in label or "could not" in label) else "warn"
        said, actual = int(m.group(1)), counts.get(field)
        ok = actual is not None and said == actual
        claims.append(_claim("count", m.group(0), ok, f"text says {said}, the facts say {actual}"))
        if not ok:
            problems.append(f"says {said} {label} but the facts have {actual}")
    for m in CHECK_COUNT_CLAIM.finditer(text):
        said = int(m.group(1))
        total, skipped = counts.get("total"), counts.get("skipped", 0)
        ok = total is not None and said in (total, total - skipped)
        claims.append(_claim("count", m.group(0), ok, f"text says {said} checks, the facts have {total}"))
        if not ok:
            problems.append(f"says {said} checks ran but the facts have {total} ({skipped} skipped)")
    if verdict in ("flagged", "attention"):
        for sent in sentences(text):
            if ALL_CLEAR.search(sent) and not NEGATION_NEAR.search(re.sub(ALL_CLEAR, "", sent)):
                claims.append(_claim("stance", sent, False, "says all is in order but the verdict is " + verdict))
                problems.append(f"calls everything in order although the verdict is '{verdict}'")
    elif verdict == "clear":
        for sent in sentences(text):
            hit = FAILURE_WORDS.search(sent)
            if hit and not NEGATION_NEAR.search(sent) and not any(q in sent for q in idx.quotable):
                claims.append(_claim("stance", sent, False, "reports a failure but the verdict is clear"))
                problems.append(f"mentions '{hit.group(0)}' although the verdict is 'clear'")
    return claims, problems


def _coverage(text: str, idx: FactIndex, audience: str) -> dict[str, Any]:
    stems_in_text = set(content_stems(text))
    bad = [c for c in idx.facts.get("checks", []) if c["status"] in ("fail", "warn")]
    if audience == "citizen":
        required = 1 if bad else 0
        ok = bool(SIGNAL_WORDS.search(text)) if bad else True
        return {"required": required, "mentioned": required if ok else 0, "missing": [] if ok else ["officer-review"]}
    missing = []
    for c in bad:
        label_stems = set(content_stems(c["label"]))
        quoted = c["label"].rstrip(".").lower() in text.lower()
        overlap = len(label_stems & stems_in_text) / len(label_stems) if label_stems else 1.0
        if not (quoted or overlap >= 0.6):
            missing.append(c["id"])
    return {"required": len(bad), "mentioned": len(bad) - len(missing), "missing": missing}


def _sentence_support(text: str, idx: FactIndex) -> list[dict[str, Any]]:
    vocab = system_vocabulary()
    out = []
    for sent in sentences(text):
        stems = content_stems(sent)
        # identifiers, numbers and dates are judged as entities; here only words
        stems = [s for s in stems if not re.search(r"\d", s)]
        unknown = sorted({s for s in stems if s not in idx.stems and s not in vocab})
        score = 1.0 if not stems else round(1 - len([s for s in stems if s in unknown]) / len(stems), 3)
        sset = set(stems)
        by = sorted(((len(sset & cs), cid) for cid, cs in idx.check_stems.items() if len(sset & cs) >= 2), reverse=True)[:3]
        out.append({"text": sent, "score": score, "supportedBy": [cid for _n, cid in by], "unknownWords": unknown[:8],
                    "weak": bool(stems) and score < 0.6 and len(stems) >= 4})
    return out


def measure(text: str, facts: dict[str, Any], audience: str, threshold: float = 0.8) -> dict[str, Any]:
    """Measure `text` against `facts`. Pure and deterministic."""
    idx = FactIndex(facts)
    text = (text or "").strip()
    if not text:
        return {"version": VERSION, "audience": audience, "passed": False, "threshold": threshold,
                "scores": {"entity": 0.0, "sentenceSupport": 0.0, "consistency": 0.0, "coverage": 0.0,
                           "faithfulness": 0.0, "hallucinationRate": 1.0},
                "claims": {"total": 1, "supported": 0, "unsupported": [_claim("empty", "", False, "empty text")]},
                "decisionLanguage": [], "verdictConsistency": {"ok": False, "problems": ["empty text"]},
                "coverage": {"required": 0, "mentioned": 0, "missing": []}, "sentences": [],
                "hardViolations": ["empty text"], "feedback": "The text was empty."}
    ent_claims, _ = _entities(text, idx)
    cons_claims, problems = _consistency(text, idx)
    decisions = _decision_violations(text, idx)
    cov = _coverage(text, idx, audience)
    sents = _sentence_support(text, idx)

    claims = ent_claims + cons_claims + [_claim("decision", d, False, "decision without the Collector") for d in decisions]
    # one claim per sentence for the decision test, so a clean text has something to count
    claims_total = len(claims) + max(0, len(sents) - len(decisions))
    bad = [c for c in claims if not c["supported"]]

    ent_ok = [c for c in ent_claims if c["supported"]]
    entity_score = len(ent_ok) / len(ent_claims) if ent_claims else 1.0
    words = sum(max(1, len(content_stems(s["text"]))) for s in sents) or 1
    support_score = sum(s["score"] * max(1, len(content_stems(s["text"]))) for s in sents) / words if sents else 0.0
    consistency_score = 1.0 if not problems else max(0.0, 1.0 - 0.5 * len(problems))
    coverage_score = cov["mentioned"] / cov["required"] if cov["required"] else 1.0

    hard = []
    hard += [f"invented {c['kind']}: {c['text']}" for c in ent_claims if not c["supported"]]
    hard += [f"unattributed decision: {d}" for d in decisions]
    hard += problems
    hard += [f"left out failing check: {cid}" for cid in cov["missing"] if cid != "officer-review"]
    if "officer-review" in cov["missing"]:
        hard.append("does not say an officer must look at it")

    faith = 0.45 * entity_score + 0.20 * support_score + 0.15 * consistency_score + 0.20 * coverage_score
    faith *= 0.5 ** min(len(hard), 3)
    rate = len(bad) / claims_total if claims_total else 0.0
    passed = not hard and faith >= threshold

    fb = []
    for c in ent_claims:
        if not c["supported"]:
            fb.append(f"'{c['text']}' is not in the CHECK RESULTS; remove it")
    fb += [f"do not approve or reject anything: {d}" for d in decisions[:2]]
    fb += problems[:2]
    if cov["missing"] and "officer-review" not in cov["missing"]:
        fb.append("mention every failed or warning check by its label")
    if "officer-review" in cov["missing"]:
        fb.append("say that an officer will review it")
    return {
        "version": VERSION, "audience": audience, "passed": passed, "threshold": threshold,
        "scores": {"entity": round(entity_score, 3), "sentenceSupport": round(support_score, 3),
                   "consistency": round(consistency_score, 3), "coverage": round(coverage_score, 3),
                   "faithfulness": round(faith, 3), "hallucinationRate": round(rate, 3)},
        "claims": {"total": claims_total, "supported": claims_total - len(bad), "unsupported": bad[:12]},
        "decisionLanguage": decisions, "verdictConsistency": {"ok": not problems, "problems": problems},
        "coverage": cov, "sentences": sents, "hardViolations": hard,
        "feedback": "; ".join(fb)[:400],
    }


def summarise(m: dict[str, Any]) -> dict[str, Any]:
    """The compact form kept in the signed report (everything needed to re-measure is the text + facts)."""
    return {"version": m["version"], "passed": m["passed"], "threshold": m["threshold"], "scores": m["scores"],
            "claims": {"total": m["claims"]["total"], "supported": m["claims"]["supported"]},
            "hardViolations": m["hardViolations"],
            "sentences": [{"text": s["text"], "score": s["score"], "supportedBy": s["supportedBy"], "weak": s["weak"]}
                          for s in m["sentences"]]}
