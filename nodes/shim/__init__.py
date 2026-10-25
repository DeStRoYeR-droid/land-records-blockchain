"""
The chaincode API: everything a smart contract can see and do.

Mirrors fabric-contract-api / fabric-shim, so a Fabric JS contract ports
almost line for line:

    from nodes.shim import Contract

    class LandRegistry(Contract):
        def RegisterParcel(self, ctx, survey_no, village, ...):
            caller = ctx.client_identity.get_attribute_value("role")
            ctx.stub.put_state(key, value)
            ctx.stub.set_event("ParcelRegistered", payload)
            return json.dumps(parcel)

Rules a contract must follow (the same as in Fabric):

* Deterministic. Every endorsing peer runs the function independently and
  their results must match byte for byte. No clock (use
  ctx.stub.get_tx_timestamp()), no randomness, no network calls, no reading
  files. Iterating dicts/sets is fine in Python only because insertion order
  is deterministic -- don't depend on anything that isn't.
* No read-your-writes. get_state() returns the COMMITTED value even after a
  put_state() of the same key earlier in the same transaction. Writes are only
  proposals until the block is validated.
* One event per transaction. A second set_event() replaces the first.
"""

from .contract import (
    ChaincodeStub,
    ClientIdentity,
    Context,
    Contract,
    KeyModification,
)

__all__ = ["ChaincodeStub", "ClientIdentity", "Context", "Contract", "KeyModification"]
