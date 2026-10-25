"""Contract base class, transaction context, stub and client identity."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Any, Protocol

from cryptography import x509

from ..common import crypto
from ..common.msp import Identity

COMPOSITE_NS = "\x00"
MAX_UNICODE = "\U0010ffff"


class Simulator(Protocol):
    def get(self, key: str) -> str | None: ...
    def put(self, key: str, value: str) -> None: ...
    def delete(self, key: str) -> None: ...
    def range(self, start: str, end: str) -> list[tuple[str, str]]: ...
    def history(self, key: str) -> list[dict[str, Any]]: ...
    def set_event(self, name: str, payload: str) -> None: ...


@dataclass(frozen=True)
class KeyModification:
    tx_id: str
    timestamp: str
    is_delete: bool
    value: bytes | None


class ClientIdentity:
    """Who submitted the transaction, read from their X.509 certificate."""

    def __init__(self, identity: Identity):
        self._identity = identity

    def get_id(self) -> str:
        return self._identity.id_string()

    def get_msp_id(self) -> str:
        return self._identity.msp_id

    def get_attribute_value(self, name: str) -> str | None:
        return self._identity.attr(name)

    def assert_attribute_value(self, name: str, value: str) -> bool:
        return self._identity.attr(name) == value

    def get_x509_certificate(self) -> x509.Certificate:
        return self._identity.cert


class ChaincodeStub:
    """The contract's window onto the ledger for one transaction."""

    def __init__(self, sim: Simulator, proposal: dict[str, Any]):
        self._sim = sim
        self._proposal = proposal

    # -- transaction info ---------------------------------------------------

    def get_tx_id(self) -> str:
        return self._proposal["txId"]

    def get_channel_id(self) -> str:
        return self._proposal["channel"]

    def get_tx_timestamp(self) -> dt.datetime:
        """The client-chosen timestamp, identical on every endorsing peer."""
        return crypto.parse_iso(self._proposal["timestamp"])

    def get_function_and_parameters(self) -> tuple[str, list[str]]:
        return self._proposal["fn"], list(self._proposal["args"])

    def get_creator(self) -> dict[str, str]:
        return dict(self._proposal["creator"])

    # -- state --------------------------------------------------------------

    @staticmethod
    def _check_key(key: str) -> None:
        if not isinstance(key, str) or key == "":
            raise ValueError("key must be a non-empty string")

    def get_state(self, key: str) -> bytes:
        """Committed value of `key`, or b'' if it doesn't exist (Fabric returns empty)."""
        self._check_key(key)
        value = self._sim.get(key)
        return value.encode("utf-8") if value is not None else b""

    def put_state(self, key: str, value: bytes | str) -> None:
        self._check_key(key)
        text = value.decode("utf-8") if isinstance(value, (bytes, bytearray)) else str(value)
        if text == "":
            raise ValueError("put_state needs a non-empty value; use del_state to delete")
        self._sim.put(key, text)

    def del_state(self, key: str) -> None:
        self._check_key(key)
        self._sim.delete(key)

    def get_state_by_range(self, start: str, end: str) -> list[tuple[str, bytes]]:
        if start.startswith(COMPOSITE_NS) or end.startswith(COMPOSITE_NS):
            raise ValueError("use get_state_by_partial_composite_key for composite keys")
        end = end or MAX_UNICODE
        return [(k, v.encode("utf-8")) for k, v in self._sim.range(start, end)]

    def get_state_by_partial_composite_key(self, object_type: str, attributes: list[str]) -> list[tuple[str, bytes]]:
        prefix = self.create_composite_key(object_type, attributes)
        return [(k, v.encode("utf-8")) for k, v in self._sim.range(prefix, prefix + MAX_UNICODE)]

    def get_history_for_key(self, key: str) -> list[KeyModification]:
        """Every committed version of `key`, oldest first."""
        self._check_key(key)
        return [KeyModification(h["txId"], h["timestamp"], h["isDelete"],
                                h["value"].encode("utf-8") if h["value"] is not None else None)
                for h in self._sim.history(key)]

    # -- composite keys -----------------------------------------------------

    @staticmethod
    def create_composite_key(object_type: str, attributes: list[str]) -> str:
        """Fabric's format: \\x00type\\x00attr1\\x00attr2\\x00 -- sorts by type, then attrs."""
        for part in [object_type, *attributes]:
            if COMPOSITE_NS in part or MAX_UNICODE in part:
                raise ValueError(f"composite key parts may not contain U+0000 or U+10FFFF: {part!r}")
        return COMPOSITE_NS + object_type + COMPOSITE_NS + "".join(a + COMPOSITE_NS for a in attributes)

    @staticmethod
    def split_composite_key(key: str) -> tuple[str, list[str]]:
        parts = key.split(COMPOSITE_NS)
        if len(parts) < 3 or parts[0] != "":
            raise ValueError("not a composite key")
        return parts[1], parts[2:-1]

    # -- events -------------------------------------------------------------

    def set_event(self, name: str, payload: bytes | str) -> None:
        text = payload.decode("utf-8") if isinstance(payload, (bytes, bytearray)) else str(payload)
        self._sim.set_event(name, text)


class Context:
    """What every transaction function receives as `ctx`."""

    def __init__(self, stub: ChaincodeStub, client_identity: ClientIdentity):
        self.stub = stub
        self.client_identity = client_identity


class Contract:
    """Base class for smart contracts. Public methods are transaction functions."""

    name: str = ""

    def __init__(self, name: str | None = None):
        self.name = name or self.name or type(self).__name__

    @classmethod
    def transactions(cls) -> list[str]:
        base = set(dir(Contract))
        return sorted(n for n in dir(cls)
                      if not n.startswith("_") and n not in base and callable(getattr(cls, n)))
