"""
Peers: where smart contracts run and where the ledger lives.

Fabric's execute -> order -> validate model, in this package:

  EXECUTE   endorser.py   a client sends a signed proposal; the peer runs the
                          chaincode against its CURRENT state (simulator.py),
                          records what it read and what it would write (the
                          read/write set), and signs that result. Nothing is
                          written yet.
  ORDER     (orderers)    the client sends the endorsed transaction to the
                          ordering service, which puts it in a block.
  VALIDATE  validator.py  every peer receives the block and, per transaction,
                          checks signatures, the endorsement policy and that
                          nothing it read has changed since (MVCC). Valid
                          transactions' writes are applied (ledger.py); invalid
                          ones stay in the block, marked with a reason.
"""
