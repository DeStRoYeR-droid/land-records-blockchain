"""
Signature policies: "whose signatures make this valid?"

The land registry's endorsement policy is

    AND('Org1MSP.peer', 'Org2MSP.peer', 'Org3MSP.peer')

meaning a transaction is valid only if a peer of *each* department simulated
it and signed the same result. No department can write to the ledger alone.

Grammar (the same one Fabric's peer CLI accepts):

    policy    := AND(arg, ...) | OR(arg, ...) | OutOf(n, arg, ...)
    arg       := policy | principal
    principal := 'MSPID.role'      role in member | peer | client | admin | orderer

Each signing identity may satisfy at most one principal, so
AND('Org1MSP.peer', 'Org1MSP.peer') needs two *different* Org1 peers. The
evaluator searches for an assignment with backtracking (sets are tiny).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterator, Sequence, Union

from .msp import Identity

ROLES = ("member", "peer", "client", "admin", "orderer")


@dataclass(frozen=True)
class Principal:
    msp_id: str
    role: str

    def matches(self, identity: Identity) -> bool:
        if identity.msp_id != self.msp_id:
            return False
        return self.role == "member" or identity.ou == self.role

    def __str__(self) -> str:
        return f"'{self.msp_id}.{self.role}'"


@dataclass(frozen=True)
class OutOf:
    n: int
    rules: tuple["Rule", ...]

    def __str__(self) -> str:
        inner = ", ".join(str(r) for r in self.rules)
        if self.n == len(self.rules):
            return f"AND({inner})"
        if self.n == 1:
            return f"OR({inner})"
        return f"OutOf({self.n}, {inner})"


Rule = Union[Principal, OutOf]


class PolicyError(ValueError):
    pass


_TOKEN = re.compile(r"\s*(AND|OR|OutOf|\(|\)|,|'[^']*'|\"[^\"]*\"|\d+)", re.IGNORECASE)


def parse(text: str) -> Rule:
    """Parse a policy string into a rule tree."""
    tokens: list[str] = []
    pos = 0
    text = text.strip()
    while pos < len(text):
        m = _TOKEN.match(text, pos)
        if not m:
            raise PolicyError(f"cannot parse policy near: {text[pos:pos + 20]!r}")
        tokens.append(m.group(1))
        pos = m.end()
        while pos < len(text) and text[pos].isspace():
            pos += 1
    rule, rest = _parse_rule(tokens)
    if rest:
        raise PolicyError(f"unexpected trailing tokens in policy: {rest}")
    return rule


def _parse_rule(tokens: list[str]) -> tuple[Rule, list[str]]:
    if not tokens:
        raise PolicyError("empty policy")
    head, rest = tokens[0], tokens[1:]
    if head[0] in "'\"":
        body = head[1:-1]
        if "." not in body:
            raise PolicyError(f"principal must look like 'MSPID.role', got {head}")
        msp_id, role = body.rsplit(".", 1)
        if role not in ROLES:
            raise PolicyError(f"unknown role '{role}' in {head}")
        return Principal(msp_id, role), rest
    keyword = head.upper()
    if keyword not in ("AND", "OR", "OUTOF"):
        raise PolicyError(f"expected AND/OR/OutOf or a principal, got {head!r}")
    if not rest or rest[0] != "(":
        raise PolicyError(f"expected '(' after {head}")
    rest = rest[1:]
    n = None
    if keyword == "OUTOF":
        if not rest or not rest[0].isdigit():
            raise PolicyError("OutOf needs a number first")
        n = int(rest[0])
        rest = rest[1:]
        if not rest or rest[0] != ",":
            raise PolicyError("expected ',' after the OutOf count")
        rest = rest[1:]
    rules: list[Rule] = []
    while True:
        rule, rest = _parse_rule(rest)
        rules.append(rule)
        if not rest:
            raise PolicyError("unterminated policy: missing ')'")
        if rest[0] == ",":
            rest = rest[1:]
            continue
        if rest[0] == ")":
            rest = rest[1:]
            break
        raise PolicyError(f"expected ',' or ')' but got {rest[0]!r}")
    if keyword == "AND":
        n = len(rules)
    elif keyword == "OR":
        n = 1
    assert n is not None
    if not 1 <= n <= len(rules):
        raise PolicyError(f"OutOf({n}) needs between 1 and {len(rules)}")
    return OutOf(n, tuple(rules)), rest


def _solutions(rule: Rule, ids: Sequence[Identity], used: frozenset[int]) -> Iterator[frozenset[int]]:
    """Every way `rule` can be satisfied, as the set of identity indexes used."""
    if isinstance(rule, Principal):
        for i, ident in enumerate(ids):
            if i not in used and rule.matches(ident):
                yield used | {i}
        return

    def choose(start: int, count: int, current: frozenset[int]) -> Iterator[frozenset[int]]:
        if count == rule.n:
            yield current
            return
        for j in range(start, len(rule.rules)):
            for after in _solutions(rule.rules[j], ids, current):
                yield from choose(j + 1, count + 1, after)

    yield from choose(0, 0, used)


def evaluate(rule: Rule, identities: Sequence[Identity]) -> bool:
    """True if the signing identities satisfy the policy."""
    return next(_solutions(rule, identities, frozenset()), None) is not None


def explain(rule: Rule, identities: Sequence[Identity]) -> str:
    """Human-readable reason, used in validation error messages."""
    have = ", ".join(f"{i.msp_id}.{i.ou}" for i in identities) or "no valid signatures"
    return f"policy {rule} not satisfied by [{have}]"


def majority_of(org_ids: Sequence[str], identities: Sequence[Identity], role: str = "admin") -> bool:
    """ImplicitMeta 'MAJORITY <role>': more than half of the orgs have a signer in `role`."""
    orgs_signed = {i.msp_id for i in identities if i.msp_id in org_ids and (role == "member" or i.ou == role)}
    return len(orgs_signed) * 2 > len(org_ids)
