"""
Installed chaincode: loading a contract and pinning exactly which code it is.

The channel config records each chaincode's *definition*: name, version,
sequence, endorsement policy and the packageHash -- the SHA-256 of its source
files. A peer computes the hash of the code it actually has on disk and only
endorses when the two match. So if one department quietly edits its copy of
the contract, its peer stops endorsing instead of signing different results.

To change the contract for everyone: edit chaincode/<name>/, then run
scripts/upgrade_chaincode.py. That commits a config transaction with the new
packageHash, signed by a majority of the organisations' admins (Fabric's
"approve for my org" + "commit"). Peers reload the code when that config
block arrives.

Fabric runs chaincode in a separate container; here it runs inside the peer
process for simplicity. The isolation you lose is crash/resource isolation,
not anything about consensus.
"""

from __future__ import annotations

import hashlib
import importlib
import json
import sys
from pathlib import Path
from typing import Any

from ..common import config
from ..shim import Context, Contract


def source_dir(module: str) -> Path:
    return config.ROOT / Path(*module.split("."))


def package_hash(module: str) -> str:
    """SHA-256 over every .py file of the chaincode (path + contents), sorted."""
    root = source_dir(module)
    h = hashlib.sha256()
    for path in sorted(root.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        h.update(path.relative_to(root).as_posix().encode() + b"\x00")
        h.update(path.read_bytes() + b"\x00")
    return h.hexdigest()


class ChaincodeError(Exception):
    pass


class InstalledChaincode:
    def __init__(self, name: str, module: str):
        self.name = name
        self.module_path = module
        self.load()

    def load(self) -> None:
        # Drop any cached copy so edited files are picked up on reload.
        for mod in [m for m in sys.modules if m == self.module_path or m.startswith(self.module_path + ".")]:
            del sys.modules[mod]
        self.package_hash = package_hash(self.module_path)
        module = importlib.import_module(self.module_path)
        contracts = getattr(module, "contracts", None)
        if not contracts:
            raise ChaincodeError(f"{self.module_path} does not export `contracts = [YourContract]`")
        cls = contracts[0]
        if not issubclass(cls, Contract):
            raise ChaincodeError(f"{cls.__name__} must subclass nodes.shim.Contract")
        self.contract: Contract = cls()
        self.functions = cls.transactions()

    def invoke(self, ctx: Context, fn: str, args: list[str]) -> str:
        if fn not in self.functions:
            raise ChaincodeError(f"function '{fn}' is not defined in chaincode {self.name}")
        if not all(isinstance(a, str) for a in args):
            raise ChaincodeError("all arguments must be strings")
        method = getattr(self.contract, fn)
        try:
            result = method(ctx, *args)
        except TypeError as exc:
            if "positional argument" in str(exc):
                raise ChaincodeError(f"{fn}: wrong number of arguments ({exc})") from exc
            raise
        if result is None:
            return ""
        return result if isinstance(result, str) else json.dumps(result)

    def describe(self) -> dict[str, Any]:
        return {"name": self.name, "module": self.module_path, "packageHash": self.package_hash,
                "functions": self.functions}
