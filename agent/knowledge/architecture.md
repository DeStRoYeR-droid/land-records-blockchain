# LandChain architecture (context for the validator's explanations)

## Overview
LandChain is a permissioned blockchain for land registration in Telangana. Three government departments share one ledger: Registration (Org1MSP), Revenue (Org2MSP) and the Collectorate (Org3MSP). No department can change a land record on its own. The nodes are written in Python and follow Hyperledger Fabric's design: certificate authorities, Raft orderers and endorsing peers.

## Departments and roles
Registration (Org1) registers citizens and parcels, opens transfer applications and anchors document fingerprints. Its roles are registrar, clerk and portal. Revenue (Org2) independently re-verifies documents and records the field survey. Its roles are verifier and surveyor, and it hosts the validator agent (role agent). The Collectorate (Org3) makes the final human decision. Its role is collector. Every role is written into the officer's X.509 certificate by the department's own certificate authority, and the chaincode reads it on every transaction.

## Application lifecycle
A transfer moves through these states: SUBMITTED (the clerk opened the file) then WITH_REVENUE (the registrar forwarded it to Revenue; this is the Org1 to Org2 handoff) then DOCS_VERIFIED (the Revenue verifier's fingerprints matched) then SURVEYED (the surveyor measured the land) then WITH_COLLECTORATE (forwarded to the Collector) then APPROVED or REJECTED. A fingerprint mismatch sets FLAGGED. The Collector can reject from any open state. Only an approval moves ownership.

## Execute, order, validate
Every write is a transaction that goes through three steps. Execute: a peer of every department runs the chaincode on its current state and signs the result, without writing anything. Order: the three Raft orderers agree on one sequence of transactions and pack them into hash-chained blocks. Validate: every peer re-checks signatures, the endorsement policy and that nothing it read has changed (MVCC), then applies the valid writes. Invalid transactions stay in the block, marked with a reason such as MVCC_READ_CONFLICT.

## Endorsement policy
The chaincode's endorsement policy is AND('Org1MSP.peer','Org2MSP.peer','Org3MSP.peer'). A transaction is valid only if a peer of each department simulated it and signed the identical result.

## Raft consensus
Three orderers run Raft. One is the leader; it cuts blocks and replicates them. If the leader stops, the others hold a pre-vote and then an election, and a new leader continues within a few seconds. Two of the three orderers must be running.

## Citizen identities and consent
Each citizen holds an Ed25519 key in their browser; their identifier is a did:key. After DigiLocker KYC, the Registration department records which did:key belongs to which ownerRef (an opaque hash, never an Aadhaar number). A transfer can only be opened when both the seller and the buyer signed the same transfer intent (survey number, parties, consideration). The chaincode verifies both signatures, and each intent can be used once.

## Documents and DigiLocker
Documents such as the sale deed and the encumbrance certificate stay in DigiLocker. Only their SHA-256 fingerprints go on the ledger. The same fingerprint can be anchored to only one application, so a deed cannot back two sales. Departments handling a file may pull its documents; every access is written to an audit log the citizen can read.

## The Agentic AI Validator
The validator is an automated assistant that works for the Revenue department. It listens for the HandoffToRevenue event, so it starts as soon as Registration forwards a file. It gathers evidence with tools: it reads the application, the parcel and its ownership history from the ledger, pulls the filed documents from DigiLocker and resolves the parties' verified names. It then runs fixed checks. If a check finds a problem, it follows up with more tools before reporting, for example fetching the issuer's original fingerprint when a document fingerprint does not match.

## Validator checks
The checks are: document fingerprints match the ledger; the filed documents contain no instructions aimed at an AI reader (a deed that tries to talk to the validator is itself a finding); each document is anchored only to this application; the sale deed and encumbrance certificate are present; the deed describes this parcel (survey number and extent); the names on the deed match the parties and their DigiLocker records; the consideration on the deed matches the application; the documents come from the seller's own locker; the encumbrance certificate shows no charges; both parties' consents are DID-signed; no rapid re-sale pattern; no prior flagged or rejected applications; the price is in line with comparable approved transfers in the district; the parcel has a surveyed boundary on the ledger; no other registered parcel's boundary overlaps it. That makes 15 fixed checks, plus a 16th check holding the model's own investigation.

## The investigation
After the fixed checks the language model investigates the open leads itself. It is given a brief and read-only tools: nearby sale prices, the parties' other files, a parcel's chain of ownership, its neighbours, overlapping boundaries, every file on a parcel, and the filed documents. It calls one tool at a time, reads the result, chooses the next, and finishes with findings that cite the call ids they rest on. It may make at most 5 tool calls. A finding is accepted only if every number, name, date and identifier in it appears in the results it cites and it states no decision; otherwise it is refused and the reason is kept. Accepted concerns become one check with status warn, so the model can raise a file to attention but never fail it, clear it, approve or reject it.

## The geospatial database
Every parcel's boundary is a GeoJSON polygon on the ledger; the ledger refuses a boundary whose area differs from the recorded area by more than 5%. The gateway projects the boundaries into an OGC GeoPackage with an R*Tree spatial index, which answers map-window searches, nearest parcels, the parcel under a point, neighbours and overlaps. It is rebuilt from the ledger with ./landchain.sh geo-rebuild.

## Chain of ownership and transfer records
OwnershipChain on the ledger lists every owner of a parcel, how and when they acquired it. Every transfer has a public record anyone can download (people as initials, no amounts) and a party copy for the seller and the buyer, each an OpenPGP cleartext-signed text file signed by the Records Office key that the Collector attests on the ledger.

## How the validator is built
The validator is a LangGraph state machine running on LangChain tools and a ChatOpenAI client pointed at oMLX. The steps are plan, gather, run the checks, investigate (the model's own tool loop, a subgraph of think and act), compute the verdict by rule, draft the explanations, measure them for hallucination, reflect (the model reviews a rejected draft and chooses to redraft or hand over to the template), redraft or replace them, compose the report and certificate, sign, verify independently, anchor, and read the anchor back from the ledger. The state is checkpointed to SQLite after every step, so a run that was interrupted resumes where it stopped.

## Hallucination measurer
Each explanation is scored without any model. Numbers, identifiers, dates and names in the text must appear in the facts. Sentences that approve, reject or recommend without naming the Collector are rejected. The verdict and the counts the text states must match the rules. Every failed or warning check must be mentioned. The result is a faithfulness score from zero to one and a hallucination rate. A text that fails goes back to the model, which reads the complaints and chooses to redraft it once or hand over to the template writer. An optional second opinion from the model is recorded but never decides.

## Independent verifier
After signing, a separate module re-checks the stored files: the JSON shape, canonical bytes, content identifier, signature and signer, the verdict rule, that the report decides nothing, the facts hash, both explanations measured again, the certificate and the trace. A report that fails is quarantined and never anchored. The same verifier runs on anyone's computer with python -m agent.verifier.

## Verdict rule
The verdict is computed by rules, not by the language model: flagged if any check fails, attention if any check warns, clear otherwise. Confidence is the share of checks that completed with full evidence.

## What the validator cannot do
The validator has an identity, not an authority. Its certificate (Org2MSP, role agent) can only anchor its own report. The chaincode refuses it for every decision: it cannot verify documents for Revenue, record a survey, forward a file, approve or reject. The application's status does not change when a report is anchored. The Revenue verifier and the Collector review the report and decide. The language model chooses how to investigate and writes the explanations; it never decides.

## Report signing and anchoring
Each report is canonical JSON. The validator signs it with an OpenPGP detached signature using an Ed25519 key. A Revenue verifier, a human, attested that key's fingerprint on the ledger; the agent cannot register its own key. The report and its reasoning trace are stored under IPFS-compatible content identifiers (CIDv1, bafkrei...). The report's SHA-256, its CID and the verdict are anchored on the ledger by the agent's own transaction.

## Verifying a report
Anyone can verify a report without ledger access: gpg --verify report.json.asc report.json with the validator's public key, then compare the key fingerprint with the one attested on the ledger, and compare the report's SHA-256 with the anchored hash. The citizen dashboard does the same in the browser with OpenPGP.js.

## Glossary
DID: decentralized identifier; here a did:key that is the citizen's public key. CID: content identifier, a name derived from the content's hash. MSP: membership service provider, the rule that says which certificate authority's certificates belong to a department. Chaincode: the smart contract. Orderer: a node that sequences transactions into blocks. MVCC: the check that nothing a transaction read changed before it was committed. ownerRef: an opaque hash that identifies a citizen on the ledger without personal data.
