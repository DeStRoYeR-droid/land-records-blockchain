# The agentic AI validator

**LangGraph** runs the pipeline and the agent loop, **LangChain** supplies the tools, tool calling, prompts and model client, **oMLX** serves the model locally (Qwen2.5-3B-Instruct-4bit). The model **chooses how to investigate** a file with read-only tools, **decides whether a rejected draft is worth redrafting**, and **writes** the explanations. It does not decide the outcome: fixed checks and a written rule do. Everything the model says is grounded or measured by code, and everything the agent produces is verified by a separate program before it is anchored.

## What the model decides, and what it cannot

| The model decides | Code decides |
|---|---|
| which tools to call, with which arguments, in which order, and when to stop (within a budget) | the 15 fixed checks and the verdict rule |
| what its findings are, citing the calls they rest on | whether a finding is accepted (the grounding check) |
| after a rejected draft: redraft with its own fix, or hand over to the template | how many drafts are allowed, and whether a text passes (the measurer) |
| the wording of the officer and citizen explanations | what is signed, verified and anchored |

An accepted *concern* becomes one more check, `agent_investigation`, with status `warn`. The verdict rule (`flagged` if any check fails, `attention` if any warns) is unchanged, so the model can raise a file from *clear* to *attention*. It can never fail a check, clear a failed one, flag, approve or reject. Every tool it can reach is read-only.

## The graph (`agent/graph.py`, 19 nodes, plus the agent loop as a subgraph)

```
plan → gather → run_checks → investigate → rule_verdict → prepare → draft ⇄ measure ⇄ reflect → (fallback) → accept ─┐
                                  │                                   ▲                                           │
                (agent loop: think ⇄ act, agent/agentic.py)           └───────────── next audience ───────────────┘
accept → (judge) → compose → sign → verify → anchor_report → confirm → finish
                                       └──(fails)──▶ quarantine → finish
```

* **gather** reads the file with LangChain `StructuredTool`s (`agent/lc_tools.py`): ledger application, parcel, history, lists, citizens, DigiLocker holders and documents. Every call is in the trace with its reason.
* **run_checks**: 15 fixed checks (`agent/checks.py`): document fingerprints, no instructions aimed at an AI, anchor uniqueness, required documents, deed matches parcel, names, consideration, seller's own locker, encumbrance, DID-signed consents, re-sale velocity, prior flags, district valuation, **the parcel has a surveyed boundary**, **no other parcel's boundary overlaps it** (asked of the geospatial database).
* **investigate**: the agent loop below.
* **rule_verdict**: computed by code from all the checks.
* **draft → measure → reflect**: for officers, then citizens. A text the measurer rejects goes to **reflect**: the model reads the checker's complaint and answers `{"action": "redraft" | "template", "fix": "…"}`. On *redraft* its own fix is added to the next prompt; on *template* (or after `LANDCHAIN_DRAFT_ATTEMPTS` drafts, whatever it says) **fallback** writes the text from a template.
* **judge** (optional), **compose / sign / verify**, **anchor_report → confirm**, **quarantine**: as before. State is checkpointed to SQLite after every node; a killed run resumes.

## The agent loop (`agent/agentic.py`) and the investigation (`agent/investigation.py`)

```
START → think ──tool calls──▶ act ──▶ think …        think ──finish / budget / 3 bad turns / model error──▶ END
```

* **think**: one model turn with the tools offered. oMLX parses Qwen's tool calls and returns OpenAI `tool_calls`; if a server returns them as `<tool_call>{…}</tool_call>` text instead, or refuses the `tools` parameter, the loop parses the text or switches to describing the tools in the prompt (`LANDCHAIN_AGENT_TOOLS=auto|native|text`).
* **act**: each call is checked before it runs: the tool must exist, its arguments must validate against the tool's schema, it must not repeat an earlier call. Its result goes back labelled with a call id (`c1`, `c2`, …).
* **budget**: at most `LANDCHAIN_AGENT_MAX_CALLS` (5) calls and `LANDCHAIN_AGENT_MAX_TURNS` (7) turns; on the last turn `finish` is the only tool offered. Three invalid turns in a row stop it.

The model starts from a **brief** built by code: the file, the fixed results, and the *open leads* the fixed checks cannot settle (is the price odd for this neighbourhood? do these people appear in other files? does the chain of ownership or the land next door show anything? who else claims an overlapping boundary? what does the deed actually say?). Its tools:

| Tool | Returns |
|---|---|
| `nearby_sale_prices(radius_m)` | approved sales of parcels near this one (geospatial `near`), price per sq.m, median, this file's % of it |
| `party_other_files(party)` | other files where the seller or buyer is a party, with outcomes and verdicts |
| `parcel_ownership_chain(survey_no)` | every owner, how and when they acquired it |
| `parcel_neighbours(survey_no, within_m)` | parcels sharing a boundary (geospatial), shared length |
| `parcel_boundary_overlaps(survey_no)` | parcels whose boundaries overlap (geospatial), overlap area |
| `parcel_files(survey_no)` | every file ever opened on a parcel |
| `read_document(doc_type)` | the filed deed or EC text, sanitised |
| `finish(summary, findings)` | ends the loop; each finding: `claim`, `severity` (concern/info), `evidence` (call ids) |

People other than the two parties appear by initials in tool results (the report is shown to the parties).

### Grounding (`agent/grounding.py`)

A finding is accepted only if it cites calls that were made and succeeded; every number, identifier, date and name in it appears in those calls' results (or in the brief); every small count ("3 other files") is a count those results contain; and it does not recommend or state a decision. Refused findings stay in the report, with the reason. The independent verifier imports this module and runs it again on the signed report.

## What it produces

| File | What |
|---|---|
| `report.json` | schema `landchain.validation-report/v3`: verdict, 16 checks (15 fixed + the investigation) with evidence, **`investigation`** (brief facts, every turn and call with its result, accepted and refused findings, budget, protocol, stop reason), both explanations, `quality` (hallucination scores, attempts, **reflections**, judge), `provenance` (graph, libraries, facts hash, prompt templates hash, model), certificate hash |
| `report.json.asc`, `certificate.txt(.asc)`, `trace.json` | as before; the certificate lists the model's tool calls and its accepted and refused findings |

## Hallucination measurer (`agent/quality.py`)

Unchanged: entity, consistency, coverage and sentence support; `faithfulness = 0.45·entity + 0.20·support + 0.15·consistency + 0.20·coverage`, halved per hard violation; pass at ≥ 0.8 with no hard violation. The investigation's accepted findings are part of the checked facts, so the explanations may cite them.

## Independent verifier (`agent/verifier.py`)

Everything before (JSON shape, canonical bytes, CID, OpenPGP signature and signer, freshness, verdict re-derived, no decision fields, facts hash, explanations re-measured, certificate, trace), plus for v3: every tool call that **ran** was a read-only tool; every accepted finding is grounded; every refused finding really fails grounding; the investigation's check says what its findings say and never fails a file.

```bash
python -m agent.verifier report.json report.json.asc key.asc --trace trace.json \
       --certificate certificate.txt --certificate-sig certificate.txt.asc
```

## The Ask box (`agent/qa.py`)

Also an agent loop: the model looks things up with read-only tools (`search_notes`, `read_parcel`, `ownership_chain`, `read_application`, `parcels_in_village`, `network_status`, `current_run`) and answers in plain text; every figure in the answer must appear in what the tools returned, or it is listed as not verified. Offline, it returns the matching sections of the notes.

## oMLX setup

1. Install and start oMLX; load `Qwen2.5-3B-Instruct-4bit`.
2. oMLX Admin → Auth: create an API key.
3. `cp landchain.env.example landchain.env`; set `OMLX_BASE_URL` (e.g. `http://127.0.0.1:8000/v1`), `OMLX_API_KEY`, `OMLX_MODEL`.
4. `./landchain.sh restart agent`. **/agent → How it is built** shows the engine, model, tool protocol and "API key: configured (never shown)".

Expect each investigation to take several model turns; on Apple Silicon a 3B model does a turn in a few seconds. If oMLX is down, there is no investigation (its check says so), the template writes the texts, and everything else still runs.

## Tests

```bash
.venv/bin/python -m pytest tests -q   # includes the agent loop (tests/test_agentic.py) and grounding
./landchain.sh check-agent            # the live network against a scripted model
```

`check-agent` drives `scripts/dev/fake_omlx.py`, a stand-in that speaks oMLX's API including tool calls. Writer modes: `good, hallucinate, verdict, name, count, stance, flaky, reflect_template, offline`. Agent modes: `liar` (a finding its results do not support), `loop` (repeats a call), `badtool` (an unknown tool, then bad arguments), `noisy` (prose instead of calls). Tool styles: `native`, `text` (`<tool_call>` blocks), `refuse` (400 on `tools`). Every report is verified with the verifier and with gpg. The fake is scripted: it shows the agent's machinery works and fails safely, not how well Qwen2.5-3B investigates; run `check-agent` against your own oMLX for that.
