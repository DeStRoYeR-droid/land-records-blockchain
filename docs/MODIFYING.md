# Making it your own

## Change the chaincode
Edit `chaincode/landrec/landrec.py`, then `.venv/bin/python scripts/upgrade_chaincode.py`: the sequence goes up by one and all three departments' admins approve, as in Fabric's lifecycle. `tests/test_chaincode.py` runs the contract in-process against the real peer simulator.

## Change a node
`nodes/peer/*.py` (endorse, validate, commit, events), `nodes/orderer/raft.py` (Raft), `nodes/ca/` (certificates), `nodes/common/` (blocks, policy, MSP, canonical JSON). After editing: `./landchain.sh restart peer0.org1`. Kill the Raft leader (`./landchain.sh kill orderer1`) and watch **/officials/network**.

## Add a validator check
In `agent/checks.py` write `check_x(ctx) -> result(...)` and add it to `CHECKS`. Each check returns `pass | warn | fail | skipped`, a detail line, and evidence. Add its id to the fixtures in `tests/agent_fixtures.py` if the measurer should expect it. The verdict rule, report, certificate, verifier and dashboards pick it up with no further change.

## Give the investigating agent a tool
In `agent/investigation.py`, `build_tools()`: write an `async def my_tool(arg: type) -> dict` (read-only; return compact JSON with the numbers a finding would cite), add it to `specs` with a one-line description, and add its name to `READ_ONLY_TOOLS` in `agent/verifier.py` (the verifier refuses a report whose agent ran anything else). Budget and protocol: `LANDCHAIN_AGENT_MAX_CALLS`, `LANDCHAIN_AGENT_MAX_TURNS`, `LANDCHAIN_AGENT_TOOLS`. The loop itself is `agent/agentic.py`; what makes a finding acceptable is `agent/grounding.py`.

## Change the map or the records
Spatial queries: `geo/store.py`; the village layout: `geo/layout.py`; boundary rules on the ledger: `chaincode/landrec/geometry.py`. Transfer record contents: `gateway/records.py` (`build` decides what each copy contains, `render` its text). See `docs/GEO.md`.

## Change what the model says
Prompts: `agent/prompts.py` (their SHA-256 is recorded in every report's provenance). Thresholds: `landchain.env`. A different model: change `OMLX_MODEL`. A different server: anything OpenAI-compatible, via `OMLX_BASE_URL`.

## Change the measurer
`agent/quality.py`. The verifier re-runs it, so changing the measurer changes what verifies; old reports record the measurer `version` they were scored with.

## Add a dashboard page
Create `web/app/(officials)/officials/<name>/page.tsx` (client component, `useDesk()` gives the officer's token), add the link in `web/lib/officer.ts`. Data: `useApi<T>("/api/...", token)`. Add the endpoint in `gateway/app.py` with `auth.need_role(...)` if it needs a role.

## Things that are deliberately simple
Officers' passwords equal their usernames; DigiLocker's OTP is `123456`; one peer per department; one machine. Replace `gateway/auth.py`, `digilocker/` and `network/nodes.yaml` for anything real.

## Testing a change
`.venv/bin/python -m pytest tests -q`, `./landchain.sh check`, `./landchain.sh check-agent`, `./landchain.sh check-web`, `cd web && npm run typecheck && npm run build`.
