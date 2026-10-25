"""
In-memory harness for chaincode tests: real certificates, fake ledger.

    h = Harness()
    h.call("Org1MSP", "registrar", "RegisterParcel", "SY-1", ...)

Each call simulates on the current state and, if it succeeds, applies its
writes immediately (one transaction per "block"), so tests read like a demo.
"""

from __future__ import annotations

import datetime as dt
import json
import tempfile
from pathlib import Path
from typing import Any

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.x509.oid import NameOID

from chaincode.landrec import LandRegistry
from nodes.ca.authority import CertificateAuthority
from nodes.common import crypto
from nodes.common.msp import MSP, MSPManager
from nodes.shim import ChaincodeStub, ClientIdentity, Context


class MemoryLedger:
    def __init__(self):
        self.state: dict[str, tuple[str, tuple[int, int]]] = {}
        self.hist: dict[str, list[dict[str, Any]]] = {}
        self.block = 0


class MemorySim:
    def __init__(self, ledger: MemoryLedger):
        self.ledger = ledger
        self.writes: dict[str, str | None] = {}
        self.event = None

    def get(self, key):
        v = self.ledger.state.get(key)
        return v[0] if v else None

    def put(self, key, value):
        self.writes[key] = value

    def delete(self, key):
        self.writes[key] = None

    def range(self, start, end):
        return [(k, v[0]) for k, v in sorted(self.ledger.state.items(), key=lambda kv: kv[0].encode())
                if start <= k < end]

    def history(self, key):
        return list(self.ledger.hist.get(key, []))

    def set_event(self, name, payload):
        self.event = {"name": name, "payload": payload}


class Harness:
    def __init__(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.cas = {m: CertificateAuthority(f"ca-{m}", m, f"{m.lower()}.test", self.tmp / m)
                    for m in ("Org1MSP", "Org2MSP", "Org3MSP")}
        self.msps = MSPManager({m: MSP(m, [crypto.cert_to_pem(ca.cert)]) for m, ca in self.cas.items()})
        self.admin = {m: self._enroll_raw(ca, "admin", "adminpw") for m, ca in self.cas.items()}
        self.ids: dict[tuple[str, str], Any] = {}
        self.ledger = MemoryLedger()
        self.contract = LandRegistry()
        self.clock = dt.datetime(2026, 10, 5, 9, 0, tzinfo=dt.timezone.utc)
        self.tx = 0

    def _enroll_raw(self, ca, name, secret):
        key = crypto.generate_private_key()
        csr = x509.CertificateSigningRequestBuilder().subject_name(
            x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)])).sign(key, hashes.SHA256())
        return ca.enroll(name, secret, csr.public_bytes(serialization.Encoding.PEM).decode())

    def identity(self, msp: str, role: str, name: str | None = None):
        name = name or f"{role}1"
        key = (msp, name)
        if key not in self.ids:
            ca = self.cas[msp]
            ca.register(self.admin[msp], name, "pw", "client",
                        {"role": role, "dept": {"Org1MSP": "registration", "Org2MSP": "revenue",
                                                "Org3MSP": "collectorate"}[msp]})
            self.ids[key] = self.msps.deserialize({"mspId": msp, "cert": self._enroll_raw(ca, name, "pw")})
        return self.ids[key]

    def call(self, msp: str, role: str, fn: str, *args: str, name: str | None = None) -> Any:
        ident = self.identity(msp, role, name)
        self.tx += 1
        self.clock += dt.timedelta(minutes=1)
        proposal = {"txId": f"tx{self.tx:04d}", "channel": "landchannel", "fn": fn, "args": list(args),
                    "creator": ident.wire(), "timestamp": crypto.iso(self.clock)}
        sim = MemorySim(self.ledger)
        ctx = Context(ChaincodeStub(sim, proposal), ClientIdentity(ident))
        out = getattr(self.contract, fn)(ctx, *args)
        self.ledger.block += 1
        for k, v in sim.writes.items():
            if v is None:
                self.ledger.state.pop(k, None)
            else:
                self.ledger.state[k] = (v, (self.ledger.block, 0))
            self.ledger.hist.setdefault(k, []).append(
                {"txId": proposal["txId"], "timestamp": proposal["timestamp"], "value": v, "isDelete": v is None})
        self.last_event = sim.event
        return json.loads(out) if out else None
