"""
landrec -- the land registry chaincode.

Like Fabric's Node.js index.js, the package exports `contracts`; the peer
instantiates the first one and exposes its public methods as transaction
functions.
"""

from .landrec import LandRegistry

contracts = [LandRegistry]

__all__ = ["LandRegistry", "contracts"]
