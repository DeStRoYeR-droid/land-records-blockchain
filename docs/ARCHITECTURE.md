# Architecture

## The three layers

```
 browser (Next.js)  ──/gw/*──▶  gateway (FastAPI, :13900)  ──▶  peers (:13151/13251/13351)
   citizen key (Ed25519)          auth, role checks, intents       ▲  endorse / query / events
   OpenPGP.js verify              DigiLocker client                │
                                                                  orderers ×3 (Raft)
 agent (:13950)  ◀── block events from peer0.org2                  │
   LangGraph validator ──────── anchors its own report ───────────┘
```

* The **browser** never holds an officer's certificate or talks to a peer. The **gateway** holds the officers' certificates (looked up by session) and submits transactions *as that officer*, so the chaincode sees the real role in the X.509 certificate.
* **Citizens** are different: they hold their own key. The gateway only relays; the chaincode verifies the citizens' Ed25519 signatures against the `did:key` registered on the ledger.
* The **agent** is a Revenue-department identity (`agent1`, role `agent`). It listens to block events, reads the ledger and DigiLocker with read-only tools, and may call exactly one write: `SubmitValidationReport`.

## Execute → order → validate

1. **Execute**: the gateway sends a proposal to one peer of each department; each runs the chaincode against its own state, records a read/write set, and signs the result.
2. **Order**: the signed transaction goes to the Raft leader (3 orderers; 2 must be up), which cuts hash-chained blocks.
3. **Validate**: every peer re-checks creator signatures, the endorsement policy `AND(Org1, Org2, Org3)` and MVCC (nothing the transaction read changed), then applies valid writes to its SQLite world state. Invalid transactions stay in the block with a reason code.

Code: `nodes/peer/` (simulator, validator, ledger), `nodes/orderer/raft.py`, `nodes/common/{block,policy,msp,crypto}.py`, `nodes/ca/`.

## Identity

* Each department has a CA; certificates carry `role=` and `dept=` attributes. The chaincode reads them on every call (`WhoAmI`).
* Citizens: `did:key` (Ed25519, multibase base58btc `z6Mk…`), created in the browser (`web/lib/did.ts`), registered on the ledger by `portal1` after DigiLocker KYC. Sign-in signs a one-time challenge, so the OTP alone is not enough.
* The agent: an X.509 identity for ledger writes, a `did:key`, and an OpenPGP key (`agent/pgp.py` writes OpenPGP v4 EdDSA packets by hand). A Revenue verifier attests the OpenPGP fingerprint on the ledger; the agent cannot attest itself.

## The land-transfer lifecycle

`SUBMITTED → WITH_REVENUE → DOCS_VERIFIED → SURVEYED → WITH_COLLECTORATE → APPROVED | REJECTED`, with `FLAGGED` as a side exit when document fingerprints differ. Only `ApproveTransfer` by the Collector moves ownership. Nothing the validator does changes the status.

| Role | Does |
|---|---|
| registrar | register parcels, issue deeds/ECs into DigiLocker, forward to Revenue |
| clerk | open a file from a two-party-signed intent, anchor the seller's documents (needs the seller's OTP) |
| verifier | re-fetch and hash the documents independently, forward to the Collectorate, ask for re-validation |
| surveyor | record the measured area and boundaries |
| collector | approve or reject, with remarks |

## Chaincode

`chaincode/landrec/landrec.py` (Python). Functions include `RegisterCitizen`, `RegisterParcel` (with the parcel's GeoJSON boundary, checked by `geometry.py`), `OwnershipChain`, `RegisterRecordsKey`/`ReadRecordsKey`, `SubmitTransferApplication` (verifies both citizens' signatures), `AnchorDocument`, `ForwardToRevenue`, `VerifyDocuments`, `RecordSurvey`, `ForwardToCollectorate`, `ApproveTransfer`, `RejectApplication`, `AttestAgentKey`, `SubmitValidationReport`, `VerifyValidationReport`, plus reads (`ListApplications`, `ParcelHistory`, …). Upgrading it: `scripts/upgrade_chaincode.py`.

## The gateway (`gateway/app.py`)

Public: `/api/public/*` (summary, parcels, agent, agent-key, verify/report/{sha}, verify/document/{sha}, sketch, `geo/*` map search, `ownership`, `records/{appId}`, `records-key`, `records/verify`; see `docs/GEO.md`). Officers: `/api/officer/summary`, `/api/applications*`, `/api/parcels`, `/api/ledger/*`, `/api/agent/*`. Citizens: `/api/citizen/*`. Role rules are in `auth.need_role`. The gateway refuses to anchor a document whose content no longer matches DigiLocker's recorded fingerprint.

## Persistence

`data/` holds ledgers (SQLite), the agent's runs, CID store and LangGraph checkpoints, DigiLocker's JSON store, the geospatial database (`data/geo/landchain.gpkg`, rebuildable from the ledger) and the Records Office key (`data/gateway/keys/`). `organizations/` holds all keys and certificates. `./shutdown.sh` stops processes gracefully; `./startup.sh` brings them back with everything intact. `./initial_setup.sh --force` wipes it all.
