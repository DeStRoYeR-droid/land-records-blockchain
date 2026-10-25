"""
The validator's tools as LangChain tools.

`tools.py` is the implementation (every call is traced with its reason). This
file wraps each method as a `StructuredTool`, so the graph's `gather` step
calls them the LangChain way -- by name, with a typed argument schema -- and
so the same set can be handed to any LangChain agent later. Every tool is
read-only: it can look at the ledger and at DigiLocker, and nothing else.
"""

from __future__ import annotations

from typing import Any

from langchain_core.tools import StructuredTool

from .tools import Tools


def build(t: Tools) -> dict[str, StructuredTool]:
    async def ledger_read_application(app_id: str, reason: str) -> dict[str, Any]:
        return await t.read_application(app_id, reason)

    async def ledger_read_parcel(survey_no: str, reason: str) -> dict[str, Any]:
        return await t.read_parcel(survey_no, reason)

    async def ledger_parcel_history(survey_no: str, reason: str) -> list[dict[str, Any]]:
        return await t.parcel_history(survey_no, reason)

    async def ledger_list_applications(reason: str) -> list[dict[str, Any]]:
        return await t.list_applications(reason)

    async def ledger_list_parcels(reason: str) -> list[dict[str, Any]]:
        return await t.list_parcels(reason)

    async def ledger_read_citizen(owner_ref: str, party: str, reason: str) -> dict[str, Any] | None:
        return await t.read_citizen(owner_ref, party, reason)

    async def digilocker_resolve_holder(owner_ref: str, party: str, purpose: str, reason: str) -> dict[str, Any] | None:
        return await t.resolve_holder(owner_ref, party, purpose, reason)

    async def digilocker_fetch_document(doc_id: str, purpose: str, reason: str, label: str) -> dict[str, Any]:
        return await t.fetch_document(doc_id, purpose, reason, label=label)

    async def ledger_verify_document(sha256: str, reason: str) -> dict[str, Any]:
        return await t.verify_document(sha256, reason)

    specs = [
        (ledger_read_application, "Read an application (parties, documents, consent, status) from the ledger."),
        (ledger_read_parcel, "Read a parcel record from the ledger by survey number."),
        (ledger_parcel_history, "Read a parcel's ownership history (every ledger version)."),
        (ledger_list_applications, "List every application on the ledger, for cross-file checks."),
        (ledger_list_parcels, "List every parcel on the ledger, for comparable-price checks."),
        (ledger_read_citizen, "Read which DID Registration bound to a citizen."),
        (digilocker_resolve_holder, "Ask DigiLocker for a citizen's Aadhaar-verified name (audited for the citizen)."),
        (digilocker_fetch_document, "Pull a filed document from DigiLocker (audited for the citizen)."),
        (ledger_verify_document, "Ask the ledger whether a document fingerprint is anchored, and to which application."),
    ]
    return {fn.__name__: StructuredTool.from_function(coroutine=fn, name=fn.__name__, description=desc)
            for fn, desc in specs}
