# LandChain

A land-registry blockchain for Telangana, built to be read and modified. Three departments (Registration, Revenue, the Collectorate) share one ledger. A citizen signs a sale with a key that never leaves their browser. Every parcel has its surveyed boundary on the ledger, indexed in a **geospatial database** for map search, with its **chain of ownership**. Every transfer has **PGP-signed records** to download: a public copy, and a fuller copy for each party.

An **agentic AI validator**, built with **LangGraph + LangChain** on a local **oMLX** model (Qwen2.5-3B-Instruct-4bit), reads each file. After 15 fixed checks, the **model chooses its own read-only tools** (sale prices of neighbouring parcels, the parties' other files, the chain of ownership, overlapping boundaries, the documents), reads what they return, and reports findings that must cite those results. It reviews its own rejected drafts and decides whether to redraft. A deterministic grounding check and a hallucination measurer gate everything it says; a signed report is verified independently and anchored on the ledger. It can never approve, reject or change anything.

* **The blockchain nodes are plain Python**, written in the style of Hyperledger Fabric: certificate authorities, three Raft orderers, three endorsing peers, execute-order-validate with MVCC, endorsement policy `AND(Org1, Org2, Org3)`, a SQLite ledger. No Docker, no Go, no Fabric binaries. (`fabric-reference/` keeps the original Fabric/Docker version for comparison.)
* **Three dashboards** (Next.js 16, React 19):
  * **Officials** (`/officials`): the five roles' queues and actions, application detail with the validator's evidence, parcels, transfer requests, ledger explorer with live blocks, node health.
  * **Citizens** (`/citizen`): register (a `did:key` is generated in the browser), sign in with DigiLocker OTP **plus a signature**, sell and buy land with two-party signatures, documents, who read them, and **in-browser OpenPGP verification** of the validator's reports.
  * **AI validator** (`/agent`): public status and signing key; for officers, a live view of the LangGraph running node by node, with the model's own tool calls and their results as it investigates and its words streaming in; every run with its evidence, grounded and refused findings, hallucination scores per sentence and the independent verifier's findings; quality and agent metrics; an "Ask" box that looks things up with tools.
* **Public pages**: the land register as a map (search by text, area, status, the map window or distance from a point), each parcel's boundary, neighbours, chain of ownership and signed transfer records; `/verify` (a report, a transfer record or a document, checked in your browser), `/how-it-works`, custom 404, error and loading states, `robots.txt`, `sitemap.xml`, `manifest.webmanifest`, canonical URLs, Open Graph / X cards and share images.

## Run it (macOS or Linux)

You need Python 3.10+ (3.11+ recommended), Node 20.9+, and, for the AI explanations, [oMLX](https://github.com/jundot/omlx) with a small instruct model loaded. Without oMLX everything still works: the validator uses its template writer and says so in every report.

```bash
unzip landchain.zip && cd landchain

# 1. the model (optional but recommended)
cp landchain.env.example landchain.env
#    edit landchain.env: set OMLX_API_KEY (the key from oMLX's Admin > Auth) and OMLX_MODEL

# 2. once: create certificates, genesis block, start everything, install web deps
./initial_setup.sh

# 3. any day after that
./startup.sh              # start what isn't running, then run the demo transactions
./startup.sh --no-demo    # start only
./shutdown.sh             # stop gracefully; ledgers, keys and DigiLocker data persist
```

Open <http://localhost:3000>.

| Who | Sign in |
|---|---|
| Officers | `/officials/sign-in`, username = password: `registrar1`, `clerk1`, `verifier1`, `surveyor1`, `collector1` |
| Citizens | `/citizen/register` (new) or `/citizen/sign-in`. DigiLocker OTP is always `123456` on this demo network |

Other laptops on the same Wi-Fi can open `http://<your-ip>:3000`; the browser never talks to the gateway directly, everything goes through the web app's `/gw/*` proxy.

### Try the whole story in five minutes
1. Register two citizens (two browsers or one normal + one private window). Save the key files.
2. As `registrar1`: *Register & issue*: register a parcel for citizen A (press *Check the boundary on the map*: the next free plot in the village, or the surveyor's corners; an overlap with another parcel is caught here), then issue A a sale deed and an encumbrance certificate.
3. As citizen A: *Sell land* to citizen B (review, sign). As B: *Sales and purchases*: review and sign.
4. As `clerk1`: *Transfer requests* → *Open a file*, then on the file anchor both documents (OTP `123456`).
5. As `registrar1`: *Forward to Revenue*. Watch **/agent → Live**: the graph lights up, the model picks its tools one by one and you see each result, its findings are grounded or refused, the model writes, the measurer scores, the report is signed, verified and anchored.
6. As citizen A: open the file, press **Verify in this browser**; download **my copy (seller)** and check its signature.
7. `verifier1` → verify documents → `surveyor1` → survey → `verifier1` → forward → `collector1` approve.
8. Open the parcel on **/register**: its boundary on the map, its neighbours, the chain of ownership (now two owners) and the public record of each transfer. Drop a downloaded record on **/verify → A transfer record**, or run `gpg --verify` on it.

## Useful commands

```bash
./landchain.sh status               # who is up, Raft leader, peer heights
./landchain.sh kill orderer1        # kill the Raft leader; watch another take over
./landchain.sh restart peer0.org2   # after editing nodes/peer/*.py
./landchain.sh logs agent
./landchain.sh check                # 33 checks: one transfer through the REST API, map search, ownership, signed records
./landchain.sh check-agent          # ~100 checks: the agent against a misbehaving model (lies, loops, bad tools, ...)
./landchain.sh check-web            # 86 checks: titles, meta, canonical, 404s, robots, sitemap, share images
./landchain.sh geo-rebuild          # rebuild the geospatial database from the ledger
.venv/bin/python -m pytest tests -q # 76 unit tests (chaincode, geometry, GeoPackage, agent loop, records, OpenPGP, measurer, verifier)
```

## Where things are

| Folder | What | Read |
|---|---|---|
| `nodes/` | CA, orderer (Raft), peer, SDK, shim: the blockchain | `docs/ARCHITECTURE.md` |
| `chaincode/landrec/` | the smart contract, with parcel boundaries (`geometry.py`) and the ownership chain | `docs/ARCHITECTURE.md` |
| `agent/` | the LangGraph validator: agent loop, investigation, grounding, measurer, verifier, PGP | `docs/AGENT.md` |
| `geo/` | the geospatial database: GeoPackage + R*Tree, spatial queries, village plot layout | `docs/GEO.md` |
| `gateway/` | REST API for the dashboards (auth, role checks), geo index, signed transfer records | `docs/ARCHITECTURE.md`, `docs/GEO.md` |
| `digilocker/` | the simulated DigiLocker | |
| `web/` | the Next.js dashboards | `docs/WEB.md` |
| `scripts/`, `tests/` | control script, checks, flows, tests | `docs/MODIFYING.md` |
| `fabric-reference/` | the original Hyperledger Fabric (Docker) version | |

Ports (all 13xxx): orderers 13050/13060/13070, peers 13151/13251/13351, CAs 13054/13154/13254/13354, DigiLocker 13800, gateway 13900, agent 13950, web 3000.

## Honest limits

This is a teaching system. One peer per department, all on one machine; one Raft cluster; DigiLocker, Aadhaar, OTP and the departments' accounts are simulated; officers' passwords are their usernames. The Python nodes imitate Fabric's *design*; they are not wire-compatible with Fabric. The hallucination measurer and the findings' grounding check are deterministic checks on a closed set of facts (numbers, names, dates, identifiers, decisions), not general truth detectors: a grounded finding can still be a poor one. A 3B model is a modest investigator; with a weak model the loop stops early or its findings are refused, and the fixed checks still decide the verdict. Parcel boundaries in the demo come from a generated village layout, not a real survey; the background map needs internet (the boundaries draw without it).
