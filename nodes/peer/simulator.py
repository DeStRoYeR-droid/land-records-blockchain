"""
Transaction simulation: run the chaincode WITHOUT changing anything.

While the contract runs, the simulator records

  reads    every key it read, with the version (block, tx) it saw
  writes   every key it wants to write (or delete) and the new value
  ranges   every range query and the exact (key, version) list it returned

That bundle is the read/write set. Later, when the transaction arrives in a
block, every peer re-checks the reads: if any key it read has a different
version now, someone else changed it in between, and the transaction is
marked MVCC_READ_CONFLICT instead of being applied. Range results are
re-run the same way to catch keys that appeared or vanished
(PHANTOM_READ_CONFLICT).

That is optimistic concurrency control: simulate without locks, detect
conflicts at commit time.
"""

from __future__ import annotations

from typing import Any

from .ledger import Ledger, Version


class TxSimulator:
    def __init__(self, ledger: Ledger):
        self.ledger = ledger
        self.reads: dict[str, Version | None] = {}
        self.writes: dict[str, dict[str, Any]] = {}
        self.ranges: list[dict[str, Any]] = []
        self.event: dict[str, str] | None = None

    def get(self, key: str) -> str | None:
        current = self.ledger.get_state(key)
        if key not in self.reads:
            self.reads[key] = current[1] if current else None
        return current[0] if current else None

    def put(self, key: str, value: str) -> None:
        self.writes[key] = {"key": key, "value": value, "delete": False}

    def delete(self, key: str) -> None:
        self.writes[key] = {"key": key, "value": None, "delete": True}

    def range(self, start: str, end: str) -> list[tuple[str, str]]:
        rows = self.ledger.range_scan(start, end)
        self.ranges.append({"start": start, "end": end,
                            "results": [[k, list(v)] for k, _, v in rows]})
        return [(k, value) for k, value, _ in rows]

    def history(self, key: str) -> list[dict[str, Any]]:
        # Not part of the read set: history is append-only and Fabric doesn't
        # re-validate it either.
        return self.ledger.history(key)

    def set_event(self, name: str, payload: str) -> None:
        self.event = {"name": name, "payload": payload}

    def rwset(self) -> dict[str, Any]:
        return {
            "reads": [{"key": k, "version": list(v) if v else None} for k, v in sorted(self.reads.items())],
            "writes": [self.writes[k] for k in sorted(self.writes)],
            "ranges": self.ranges,
        }
