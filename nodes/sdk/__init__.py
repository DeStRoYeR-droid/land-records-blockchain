"""
Client SDK: how an application talks to the network (Fabric Gateway's job).

    gw = Gateway.for_user("Org1MSP", "clerk1")
    await gw.evaluate("landrec", "ReadParcel", "SY-42/1A")         # query one peer
    res = await gw.submit("landrec", "RegisterParcel", ...)         # full transaction
    print(res.tx_id, res.block, res.payload)

submit() runs the whole execute -> order -> validate path:

  1. build a proposal {fn, args, creator, nonce, txId, timestamp} and sign it
  2. send it to a peer of EVERY department (the policy needs all three)
  3. check all three returned byte-identical results; if not (one peer was a
     block behind and saw older data), wait and endorse again
  4. wrap proposal + result + the three endorsements in an envelope, sign it,
     broadcast it to the ordering service
  5. wait until the peers have committed the block and report the
     validation code (VALID, MVCC_READ_CONFLICT, ...)
"""

from .gateway import (
    ChaincodeError,
    CommitError,
    EndorsementMismatch,
    Gateway,
    NetworkUnavailable,
    SubmitResult,
)

__all__ = ["ChaincodeError", "CommitError", "EndorsementMismatch", "Gateway", "NetworkUnavailable", "SubmitResult"]
