"""Chaincode behaviour: the JS contract's rules, DID consents, and the agent's limits."""

import base64
import json
import uuid

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from chaincode.landrec import didkey
from chaincode.landrec.landrec import canonical

from .harness import Harness

FPR = "A" * 40
ARMOR = "-----BEGIN PGP PUBLIC KEY BLOCK-----\n\nxx\n-----END PGP PUBLIC KEY BLOCK-----\n"


class Citizen:
    def __init__(self, ref: str):
        self.ref = ref
        self.key = Ed25519PrivateKey.generate()
        self.did = didkey.did_from_public_key(self.key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw))

    def sign(self, intent: dict) -> dict:
        return {"did": self.did, "signature": base64.b64encode(self.key.sign(canonical(intent))).decode()}


def make_intent(survey, seller, buyer, amount, created="2026-10-05T08:00:00.000Z"):
    return {"type": "landchain.transfer-intent/v1", "intentId": uuid.uuid4().hex, "surveyNo": survey,
            "sellerRef": seller.ref, "buyerRef": buyer.ref, "considerationINR": amount, "createdAt": created}


@pytest.fixture()
def world():
    h = Harness()
    seller, buyer = Citizen("ref-seller"), Citizen("ref-buyer")
    for c in (seller, buyer):
        h.call("Org1MSP", "portal", "RegisterCitizen", c.ref, c.did)
    h.call("Org1MSP", "registrar", "RegisterParcel", "SY-1", "Kazipet", "Warangal", "2400", "Ramesh Kumar", seller.ref)
    return h, seller, buyer


def open_transfer(h, seller, buyer, app="APP-1", survey="SY-1", amount=4500000, intent=None, sigs=None):
    intent = intent or make_intent(survey, seller, buyer, amount)
    consent = {"intent": intent, "seller": (sigs or {}).get("seller") or seller.sign(intent),
               "buyer": (sigs or {}).get("buyer") or buyer.sign(intent)}
    return h.call("Org1MSP", "clerk", "SubmitTransferApplication", app, survey, seller.ref, "Lakshmi Devi",
                  buyer.ref, str(amount), json.dumps(consent))


def test_full_lifecycle_and_agent_cannot_decide(world):
    h, seller, buyer = world
    app = open_transfer(h, seller, buyer)
    assert app["status"] == "SUBMITTED" and app["consent"]["sellerDid"] == seller.did
    h.call("Org1MSP", "clerk", "AnchorDocument", "APP-1", "a" * 64, "DOC-1", "SALEDEED", "SRO")
    h.call("Org1MSP", "registrar", "ForwardToRevenue", "APP-1")
    assert h.last_event["name"] == "HandoffToRevenue"

    # the validator can anchor evidence only after a human attested its key
    with pytest.raises(Exception, match="no PGP key attested"):
        h.call("Org2MSP", "agent", "SubmitValidationReport", "APP-1", "b" * 64, "bafkreiaaaaaaaaaaaaaaaaaaaa",
               "bafkreibbbbbbbbbbbbbbbbbbbb", "clear", "0", "0.9")
    with pytest.raises(Exception, match="ACCESS DENIED"):
        h.call("Org2MSP", "agent", "RegisterAgentKey", "agent1", FPR, seller.did, ARMOR)
    h.call("Org2MSP", "verifier", "RegisterAgentKey", "agent1", FPR, seller.did, ARMOR)
    anchor = h.call("Org2MSP", "agent", "SubmitValidationReport", "APP-1", "b" * 64, "bafkreiaaaaaaaaaaaaaaaaaaaa",
                    "bafkreibbbbbbbbbbbbbbbbbbbb", "clear", "0", "0.9")
    assert anchor["pgpFingerprint"] == FPR and anchor["agent"] == "agent1"
    raw = json.loads(h.ledger.state["\x00application\x00APP-1\x00"][0])
    assert raw["status"] == "WITH_REVENUE" and "validations" not in raw  # evidence never touches the file
    view = h.call("Org2MSP", "verifier", "ReadApplication", "APP-1")
    assert view["validations"][0]["verdict"] == "clear"
    assert view["trail"][-1]["role"] == "agent" and "evidence only" in view["trail"][-1]["action"]

    # identity, not authority: every decision function refuses the agent
    for fn, args in [("VerifyDocuments", ("APP-1", "[]")), ("ApproveTransfer", ("APP-1", "x")),
                     ("RejectApplication", ("APP-1", "x")), ("ForwardToCollectorate", ("APP-1",))]:
        with pytest.raises(Exception, match="ACCESS DENIED"):
            h.call("Org2MSP", "agent", fn, *args)

    h.call("Org2MSP", "verifier", "VerifyDocuments", "APP-1", json.dumps(["a" * 64]))
    h.call("Org2MSP", "surveyor", "RecordSurvey", "APP-1", "2412", "N: road")
    h.call("Org2MSP", "verifier", "ForwardToCollectorate", "APP-1")
    done = h.call("Org3MSP", "collector", "ApproveTransfer", "APP-1", "ok")
    assert done["status"] == "APPROVED"
    parcel = h.call("Org3MSP", "collector", "ReadParcel", "SY-1")
    assert parcel["ownerRef"] == buyer.ref and parcel["lastTransfer"]["fromName"] == "Ramesh Kumar"
    assert h.call("Org3MSP", "collector", "VerifyValidationReport", "b" * 64)["anchored"] is True


def test_survey_deviation_matches_javascript(world):
    h, seller, buyer = world
    open_transfer(h, seller, buyer)
    h.call("Org1MSP", "clerk", "AnchorDocument", "APP-1", "a" * 64, "DOC-1", "SALEDEED", "SRO")
    h.call("Org1MSP", "registrar", "ForwardToRevenue", "APP-1")
    h.call("Org2MSP", "verifier", "VerifyDocuments", "APP-1", json.dumps(["a" * 64]))
    app = h.call("Org2MSP", "surveyor", "RecordSurvey", "APP-1", "2412", "b")
    assert app["survey"]["deviationPct"] == 0.5 and app["survey"]["withinTolerance"] is True


def test_consent_is_required_and_verified(world):
    h, seller, buyer = world
    impostor = Citizen("ref-seller")  # same ownerRef, different key
    intent = make_intent("SY-1", seller, buyer, 4500000)
    with pytest.raises(Exception, match="not their registered DID"):
        open_transfer(h, seller, buyer, intent=intent, sigs={"seller": impostor.sign(intent)})
    forged = seller.sign(intent)
    forged["signature"] = base64.b64encode(b"\x01" * 64).decode()
    with pytest.raises(Exception, match="seller's signature on the transfer intent is invalid"):
        open_transfer(h, seller, buyer, intent=intent, sigs={"seller": forged})
    # amount changed after signing
    with pytest.raises(Exception, match="considerationINR does not match"):
        h.call("Org1MSP", "clerk", "SubmitTransferApplication", "APP-1", "SY-1", seller.ref, "Lakshmi Devi",
               buyer.ref, "100", json.dumps({"intent": intent, "seller": seller.sign(intent), "buyer": buyer.sign(intent)}))
    # a valid consent works once, never twice
    open_transfer(h, seller, buyer, intent=intent)
    h.call("Org3MSP", "collector", "RejectApplication", "APP-1", "test")
    with pytest.raises(Exception, match="already used"):
        open_transfer(h, seller, buyer, app="APP-2", intent=intent)


def test_original_attacks_still_refused(world):
    h, seller, buyer = world
    open_transfer(h, seller, buyer)
    with pytest.raises(Exception, match="ACCESS DENIED: ApproveTransfer requires an Org3MSP"):
        h.call("Org1MSP", "clerk", "ApproveTransfer", "APP-1", "self-approval")
    with pytest.raises(Exception, match="locked by pending application APP-1"):
        open_transfer(h, seller, buyer, app="APP-2")
    h.call("Org1MSP", "clerk", "AnchorDocument", "APP-1", "c" * 64, "DOC-9", "SALEDEED", "SRO")
    h.call("Org1MSP", "registrar", "RegisterParcel", "SY-2", "Hanamkonda", "Warangal", "1800", "Lakshmi Devi", buyer.ref)
    open_transfer(h, buyer, seller, app="APP-3", survey="SY-2", amount=3000000)
    with pytest.raises(Exception, match="already anchored to application APP-1"):
        h.call("Org1MSP", "clerk", "AnchorDocument", "APP-3", "c" * 64, "DOC-9", "SALEDEED", "SRO")
