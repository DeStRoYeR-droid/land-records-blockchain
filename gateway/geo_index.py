"""
Keeps the geospatial database in step with the ledger.

The ledger is the source of truth. This projector reads every parcel once at
start-up, then follows the peer's block events: whenever a transaction touches
a parcel (registered, put under a transfer, transferred, released), it reads
that parcel and its ownership chain back from the ledger and rewrites the row
in the GeoPackage. Delete the .gpkg file (or run ./landchain.sh geo-rebuild)
and it is rebuilt from the ledger.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx

from geo.store import GeoStore
from nodes.common import config, log
from nodes.common.web import read_sse

logger = log.get("geo")

PARCEL_EVENTS = {"ParcelRegistered", "ApplicationSubmitted", "TransferApproved", "ApplicationRejected"}


class GeoIndex:
    def __init__(self, ledger: Any, http: httpx.AsyncClient, user: str = "portal1"):
        self.ledger, self.http, self.user = ledger, http, user
        self.store = GeoStore(config.data_dir("geo") / "landchain.gpkg")
        self.synced_height: int | None = None
        self.last_error: str | None = None
        self.task: asyncio.Task | None = None
        self.ready = asyncio.Event()

    async def refresh(self, survey_no: str) -> bool:
        parcel = await self.ledger.q(self.user, "ReadParcel", survey_no)
        owners = None
        if parcel.get("boundary"):
            owners = (await self.ledger.q(self.user, "OwnershipChain", survey_no))["owners"]
        return self.store.upsert(parcel, owners)

    async def sync_all(self) -> int:
        parcels = await self.ledger.q(self.user, "ListParcels")
        n = 0
        for p in parcels:
            if p.get("boundary"):
                owners = (await self.ledger.q(self.user, "OwnershipChain", p["surveyNo"]))["owners"]
                n += self.store.upsert(p, owners)
        return n

    async def _surveys_in(self, block: dict[str, Any]) -> set[str]:
        out: set[str] = set()
        for t in block.get("txs", []):
            ev = t.get("event") or {}
            if ev.get("name") not in PARCEL_EVENTS:
                continue
            try:
                payload = json.loads(ev.get("payload") or "{}")
            except ValueError:
                payload = {}
            if payload.get("surveyNo"):
                out.add(payload["surveyNo"])
            elif payload.get("appId"):
                try:
                    out.add((await self.ledger.q(self.user, "ReadApplication", payload["appId"]))["surveyNo"])
                except Exception:
                    pass
        return out

    async def run(self) -> None:
        peer = config.url(config.org_peer("Org1MSP"))
        while True:
            try:
                st = (await self.http.get(f"{peer}/status", timeout=3)).json()
                start = st["height"]
                n = await self.sync_all()
                self.synced_height, self.last_error = start, None
                self.ready.set()
                logger.info("geospatial index: %d parcels with boundaries, following blocks from %d", n, start)
                async with self.http.stream("GET", f"{peer}/events", params={"start": start},
                                            timeout=httpx.Timeout(None, connect=3.0)) as resp:
                    async for _ev, data in read_sse(resp):
                        block = json.loads(data)
                        for sy in await self._surveys_in(block):
                            try:
                                await self.refresh(sy)
                            except Exception as exc:
                                logger.warning("could not index %s: %s", sy, exc)
                        self.synced_height = block["number"] + 1
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.last_error = f"{type(exc).__name__}: {exc}"
                logger.info("geospatial index waiting for the ledger (%s)", self.last_error)
                await asyncio.sleep(3)

    def start(self) -> None:
        self.task = asyncio.create_task(self.run())

    async def stop(self) -> None:
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except (asyncio.CancelledError, Exception):
                pass

    def status(self) -> dict[str, Any]:
        return {**self.store.stats(), "syncedToBlock": self.synced_height, "lastError": self.last_error}
