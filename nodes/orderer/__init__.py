"""
The ordering service: puts endorsed transactions into one agreed order.

Orderers do NOT run chaincode and do NOT check endorsements -- that is the
peers' job. They check only that the submitter is a channel member allowed to
write, batch transactions into blocks, and agree on the block sequence via
Raft (see raft.py). Peers then pull blocks with the deliver API.
"""
