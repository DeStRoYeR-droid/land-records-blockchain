"""
The peer's ledger, in one SQLite file (data/<peer>/ledger.db).

  blocks   the chain itself, every block exactly as received (+ our validation
           codes in its metadata)
  txs      txId -> (block, position, validation code); detects duplicate txIds
  state    the WORLD STATE: current value of every key, with its version =
           (block, tx) of the transaction that last wrote it
  history  every value a key ever had (for ParcelHistory / ApplicationHistory)

Fabric keeps blocks in flat files and state in LevelDB/CouchDB; the roles are
the same. The world state is only a cache of "replay every valid write in
order" -- delete it and it can be rebuilt from the blocks.

A block is committed in ONE SQLite transaction: either the block, its tx index,
its state changes and history rows all land, or none of them do. A crash in
the middle can't leave the state half-updated.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Iterable

Version = tuple[int, int]

MAX_UNICODE_SUFFIX = "\U0010ffff"


def kb(key: str) -> bytes:
    return key.encode("utf-8")


class Ledger:
    def __init__(self, path: Path):
        # isolation_level=None: we issue BEGIN/COMMIT ourselves (see commit_block)
        self.db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript(
            """
            CREATE TABLE IF NOT EXISTS blocks (number INTEGER PRIMARY KEY, hash TEXT NOT NULL, data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS txs (txid TEXT PRIMARY KEY, block INTEGER, idx INTEGER, code TEXT);
            CREATE TABLE IF NOT EXISTS state (key BLOB PRIMARY KEY, value BLOB, block INTEGER, tx INTEGER);
            CREATE TABLE IF NOT EXISTS history (key BLOB, block INTEGER, tx INTEGER, txid TEXT, ts TEXT,
                                                value BLOB, is_delete INTEGER);
            CREATE INDEX IF NOT EXISTS history_key ON history (key, block, tx);
            CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT);
            """
        )
        self.db.commit()

    # -- blocks ---------------------------------------------------------------

    def height(self) -> int:
        row = self.db.execute("SELECT MAX(number) FROM blocks").fetchone()
        return (row[0] + 1) if row and row[0] is not None else 0

    def get_block(self, number: int) -> dict[str, Any] | None:
        row = self.db.execute("SELECT data FROM blocks WHERE number=?", (number,)).fetchone()
        return json.loads(row[0]) if row else None

    def block_hash(self, number: int) -> str | None:
        row = self.db.execute("SELECT hash FROM blocks WHERE number=?", (number,)).fetchone()
        return row[0] if row else None

    def blocks_desc(self, before: int, limit: int) -> list[dict[str, Any]]:
        rows = self.db.execute("SELECT data FROM blocks WHERE number<? ORDER BY number DESC LIMIT ?",
                               (before, limit)).fetchall()
        return [json.loads(r[0]) for r in rows]

    def tx_status(self, txid: str) -> dict[str, Any] | None:
        row = self.db.execute("SELECT block, idx, code FROM txs WHERE txid=?", (txid,)).fetchone()
        return {"txId": txid, "block": row[0], "index": row[1], "code": row[2]} if row else None

    def has_tx(self, txid: str) -> bool:
        return self.db.execute("SELECT 1 FROM txs WHERE txid=?", (txid,)).fetchone() is not None

    def count_txs(self) -> dict[str, int]:
        rows = self.db.execute("SELECT code, COUNT(*) FROM txs GROUP BY code").fetchall()
        return {r[0]: r[1] for r in rows}

    # -- world state ----------------------------------------------------------

    def get_state(self, key: str) -> tuple[str, Version] | None:
        row = self.db.execute("SELECT value, block, tx FROM state WHERE key=?", (kb(key),)).fetchone()
        if row is None:
            return None
        return row[0].decode("utf-8"), (row[1], row[2])

    def get_version(self, key: str) -> Version | None:
        row = self.db.execute("SELECT block, tx FROM state WHERE key=?", (kb(key),)).fetchone()
        return (row[0], row[1]) if row else None

    def range_scan(self, start: str, end: str) -> list[tuple[str, str, Version]]:
        """Keys in [start, end), in key order (byte order of UTF-8, like Fabric)."""
        rows = self.db.execute(
            "SELECT key, value, block, tx FROM state WHERE key>=? AND key<? ORDER BY key",
            (kb(start), kb(end))).fetchall()
        return [(r[0].decode("utf-8"), r[1].decode("utf-8"), (r[2], r[3])) for r in rows]

    def history(self, key: str) -> list[dict[str, Any]]:
        rows = self.db.execute(
            "SELECT txid, ts, value, is_delete FROM history WHERE key=? ORDER BY block, tx", (kb(key),)).fetchall()
        return [{"txId": r[0], "timestamp": r[1], "value": r[2].decode("utf-8") if r[2] is not None else None,
                 "isDelete": bool(r[3])} for r in rows]

    def count_keys(self) -> int:
        return self.db.execute("SELECT COUNT(*) FROM state").fetchone()[0]

    def list_state(self, prefix: str, limit: int) -> list[dict[str, Any]]:
        rows = self.db.execute(
            "SELECT key, value, block, tx FROM state WHERE key>=? AND key<? ORDER BY key LIMIT ?",
            (kb(prefix), kb(prefix + MAX_UNICODE_SUFFIX), limit)).fetchall()
        return [{"key": r[0].decode("utf-8"), "value": r[1].decode("utf-8"), "version": [r[2], r[3]]}
                for r in rows]

    # -- meta -----------------------------------------------------------------

    def get_meta(self, key: str) -> str | None:
        row = self.db.execute("SELECT v FROM meta WHERE k=?", (key,)).fetchone()
        return row[0] if row else None

    def set_meta(self, key: str, value: str) -> None:
        self.db.execute("INSERT INTO meta (k, v) VALUES (?, ?) ON CONFLICT(k) DO UPDATE SET v=excluded.v",
                        (key, value))
        self.db.commit()

    # -- commit -----------------------------------------------------------------

    def commit_block(self, block: dict[str, Any], block_hash: str, codes: list[str], tx_ids: list[str | None],
                     updates: Iterable[dict[str, Any]], config: dict[str, Any] | None = None) -> None:
        """Atomically store a validated block and apply its valid transactions' writes.

        updates: one dict per valid tx: {txIndex, txId, timestamp, writes: [{key, value, delete}]}
        """
        number = block["header"]["number"]
        cur = self.db.cursor()
        try:
            cur.execute("BEGIN")
            cur.execute("INSERT INTO blocks (number, hash, data) VALUES (?, ?, ?)",
                        (number, block_hash, json.dumps(block)))
            for idx, (txid, code) in enumerate(zip(tx_ids, codes)):
                if txid and code != "DUPLICATE_TXID":
                    cur.execute("INSERT OR IGNORE INTO txs (txid, block, idx, code) VALUES (?, ?, ?, ?)",
                                (txid, number, idx, code))
            for upd in updates:
                for w in upd["writes"]:
                    key = kb(w["key"])
                    if w.get("delete"):
                        cur.execute("DELETE FROM state WHERE key=?", (key,))
                        value = None
                    else:
                        value = w["value"].encode("utf-8")
                        cur.execute(
                            "INSERT INTO state (key, value, block, tx) VALUES (?, ?, ?, ?)"
                            " ON CONFLICT(key) DO UPDATE SET value=excluded.value, block=excluded.block, tx=excluded.tx",
                            (key, value, number, upd["txIndex"]))
                    cur.execute("INSERT INTO history (key, block, tx, txid, ts, value, is_delete) VALUES (?,?,?,?,?,?,?)",
                                (key, number, upd["txIndex"], upd["txId"], upd["timestamp"], value,
                                 1 if w.get("delete") else 0))
            if config is not None:
                cur.execute("INSERT INTO meta (k, v) VALUES ('channel_config', ?)"
                            " ON CONFLICT(k) DO UPDATE SET v=excluded.v", (json.dumps(config),))
            cur.execute("COMMIT")
        except Exception:
            cur.execute("ROLLBACK")
            raise
