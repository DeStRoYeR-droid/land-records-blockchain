"""
Raft consensus, as the ordering service uses it.

Problem: three orderers must agree on ONE sequence of blocks, and keep
agreeing when one of them crashes. Raft solves it with a leader:

  1. Leader election. Every node starts as a follower. A follower that hears
     nothing from a leader for a randomised "election timeout" asks the others
     to vote for it. A node that gets votes from a majority (2 of 3) becomes
     leader for a numbered "term".
  2. Log replication. Only the leader appends to the log (here: each entry is
     one block). It sends new entries to followers with AppendEntries; the
     same message, empty, is the heartbeat that keeps followers from starting
     an election.
  3. Commit. Once a majority stores an entry, it is committed: it can never be
     lost or replaced, and every node applies it (writes the block).

Safety rules that make this work:
  * a node votes at most once per term, and only for a candidate whose log is
    at least as up to date as its own -> a new leader always has every
    committed entry;
  * AppendEntries carries (prevIndex, prevTerm); a follower whose log does not
    match there refuses, and the leader walks back until the logs agree, then
    overwrites the follower's divergent tail.

Three refinements from etcd-raft, which Fabric's orderer uses:
  * Pre-vote: before bumping its term, a would-be candidate asks "would you
    vote for me?" A node cut off from the network can't win that poll, so it
    can't disrupt a healthy cluster with ever-higher terms when it rejoins.
    (These are the "pre-candidate" lines in Fabric's orderer logs.)
  * Leader stickiness: a node that heard from a live leader recently refuses
    to vote someone else in.
  * Check-quorum: a leader that can't reach a majority steps down instead of
    pretending to lead.
  * A new leader appends an empty entry. Raft only lets a leader commit
    entries from ITS term; the empty entry lets it commit (and thereby confirm)
    any block left over from the previous leader without waiting for new
    traffic. Empty entries are skipped when applying.
"""

from __future__ import annotations

import asyncio
import collections
import logging
import random
import sqlite3
import time
from typing import Any, Awaitable, Callable

FOLLOWER = "follower"
PRE_CANDIDATE = "pre-candidate"
CANDIDATE = "candidate"
LEADER = "leader"

MAX_ENTRIES_PER_APPEND = 16

Transport = Callable[[str, str, dict], Awaitable[dict | None]]
ApplyFn = Callable[[int, str], None]


class NotLeader(Exception):
    def __init__(self, leader_id: str | None):
        super().__init__(f"not the leader (leader is {leader_id or 'unknown'})")
        self.leader_id = leader_id


class RaftStorage:
    """Persistent Raft state. Must hit the disk before we answer an RPC."""

    def __init__(self, db: sqlite3.Connection):
        self.db = db
        db.execute("CREATE TABLE IF NOT EXISTS raft_meta (k TEXT PRIMARY KEY, v TEXT)")
        db.execute("CREATE TABLE IF NOT EXISTS raft_log (idx INTEGER PRIMARY KEY, term INTEGER NOT NULL, data TEXT NOT NULL)")
        db.commit()
        self.term = int(self._meta("term", "0"))
        voted = self._meta("voted_for", "")
        self.voted_for: str | None = voted or None
        row = db.execute("SELECT idx, term FROM raft_log ORDER BY idx DESC LIMIT 1").fetchone()
        self._last = (row[0], row[1]) if row else (0, 0)

    def _meta(self, key: str, default: str) -> str:
        row = self.db.execute("SELECT v FROM raft_meta WHERE k=?", (key,)).fetchone()
        return row[0] if row else default

    def set_meta(self, key: str, value: str, commit: bool = True) -> None:
        self.db.execute("INSERT INTO raft_meta (k, v) VALUES (?, ?) ON CONFLICT(k) DO UPDATE SET v=excluded.v",
                        (key, value))
        if commit:
            self.db.commit()

    def meta(self, key: str, default: str = "") -> str:
        return self._meta(key, default)

    def set_term_and_vote(self, term: int, voted_for: str | None) -> None:
        self.set_meta("term", str(term), commit=False)
        self.set_meta("voted_for", voted_for or "", commit=False)
        self.db.commit()
        self.term, self.voted_for = term, voted_for

    def last_index(self) -> int:
        return self._last[0]

    def last_term(self) -> int:
        return self._last[1]

    def term_at(self, idx: int) -> int | None:
        if idx == 0:
            return 0
        row = self.db.execute("SELECT term FROM raft_log WHERE idx=?", (idx,)).fetchone()
        return row[0] if row else None

    def data_at(self, idx: int) -> str:
        row = self.db.execute("SELECT data FROM raft_log WHERE idx=?", (idx,)).fetchone()
        if row is None:
            raise KeyError(f"no raft entry at index {idx}")
        return row[0]

    def entries_from(self, idx: int, limit: int) -> list[list[Any]]:
        rows = self.db.execute("SELECT idx, term, data FROM raft_log WHERE idx>=? ORDER BY idx LIMIT ?",
                               (idx, limit)).fetchall()
        return [list(r) for r in rows]

    def first_index_of_term(self, term: int) -> int:
        row = self.db.execute("SELECT MIN(idx) FROM raft_log WHERE term=?", (term,)).fetchone()
        return row[0] or 1

    def append(self, entries: list[list[Any]]) -> None:
        self.db.executemany("INSERT INTO raft_log (idx, term, data) VALUES (?, ?, ?)",
                            [(e[0], e[1], e[2]) for e in entries])
        self.db.commit()
        self._last = (entries[-1][0], entries[-1][1])

    def truncate_from(self, idx: int) -> None:
        self.db.execute("DELETE FROM raft_log WHERE idx>=?", (idx,))
        self.db.commit()
        row = self.db.execute("SELECT idx, term FROM raft_log ORDER BY idx DESC LIMIT 1").fetchone()
        self._last = (row[0], row[1]) if row else (0, 0)


class RaftNode:
    def __init__(self, node_id: str, cluster: list[str], storage: RaftStorage, transport: Transport,
                 apply_fn: ApplyFn, applied_index: int, heartbeat_ms: int,
                 election_timeout_ms: tuple[int, int], logger: logging.Logger | None = None,
                 on_leader_change: Callable[[str | None], None] | None = None):
        self.id = node_id
        self.peers = [p for p in cluster if p != node_id]
        self.cluster_size = len(cluster)
        self.storage = storage
        self.transport = transport
        self.apply_fn = apply_fn
        self.heartbeat = heartbeat_ms / 1000
        self.election_min = election_timeout_ms[0] / 1000
        self.election_max = election_timeout_ms[1] / 1000
        self.log = logger or logging.getLogger("raft")
        self.on_leader_change = on_leader_change

        self.state = FOLLOWER
        self.leader_id: str | None = None
        self.commit_index = applied_index
        self.last_applied = applied_index
        self.next_index: dict[str, int] = {}
        self.match_index: dict[str, int] = {}
        self.last_ack: dict[str, float] = {}
        self.last_leader_contact = 0.0
        self.became_leader_at = 0.0
        self.election_deadline = 0.0
        self._reset_election_deadline()
        self._kick: dict[str, asyncio.Event] = {}
        self._campaigning = False
        self._tasks: list[asyncio.Task] = []
        self._replicators: list[asyncio.Task] = []
        self.applied = asyncio.Condition()
        self.events: collections.deque = collections.deque(maxlen=40)

    # -- helpers -------------------------------------------------------------

    @property
    def term(self) -> int:
        return self.storage.term

    def majority(self) -> int:
        return self.cluster_size // 2 + 1

    def _reset_election_deadline(self) -> None:
        self.election_deadline = time.monotonic() + random.uniform(self.election_min, self.election_max)

    def _event(self, text: str, level: int = logging.INFO) -> None:
        self.events.append({"at": time.time(), "term": self.term, "text": text})
        self.log.log(level, "%s", text)

    def _log_up_to_date(self, last_index: int, last_term: int) -> bool:
        my_term = self.storage.last_term()
        return last_term > my_term or (last_term == my_term and last_index >= self.storage.last_index())

    # -- lifecycle -----------------------------------------------------------

    async def start(self) -> None:
        self._event(f"starting as follower at term {self.term} (log length {self.storage.last_index()}, "
                    f"applied {self.last_applied})")
        self._tasks.append(asyncio.create_task(self._ticker()))

    async def stop(self) -> None:
        for task in self._tasks + self._replicators:
            task.cancel()
        await asyncio.gather(*self._tasks, *self._replicators, return_exceptions=True)

    async def _ticker(self) -> None:
        while True:
            await asyncio.sleep(0.05)
            now = time.monotonic()
            if self.state == LEADER:
                if self.cluster_size > 1 and now - self.became_leader_at > self.election_max:
                    alive = 1 + sum(1 for p in self.peers if now - self.last_ack.get(p, 0) < self.election_max)
                    if alive < self.majority():
                        self._event("check-quorum: can't reach a majority, stepping down", logging.WARNING)
                        self._become_follower(self.term, None)
            elif now >= self.election_deadline and not self._campaigning:
                self._reset_election_deadline()
                asyncio.create_task(self._campaign())

    # -- elections -----------------------------------------------------------

    async def _campaign(self) -> None:
        self._campaigning = True
        try:
            if self.cluster_size == 1:
                self.storage.set_term_and_vote(self.term + 1, self.id)
                self._become_leader()
                return
            # Phase 1: pre-vote (no term change yet)
            term_before = self.term
            self.state = PRE_CANDIDATE
            self._event(f"became pre-candidate at term {term_before} (leader {self.leader_id or 'unknown'} silent)")
            granted, voters = await self._ask_votes(term_before + 1, pre=True)
            if self.state != PRE_CANDIDATE or self.term != term_before:
                return
            if granted < self.majority():
                self._event(f"pre-vote lost ({granted}/{self.cluster_size}); staying follower")
                self.state = FOLLOWER
                return
            # Phase 2: the real election
            new_term = term_before + 1
            self.storage.set_term_and_vote(new_term, self.id)
            self.state = CANDIDATE
            self._set_leader(None)
            self._event(f"became candidate at term {new_term} (pre-votes from {', '.join(voters)})")
            granted, voters = await self._ask_votes(new_term, pre=False)
            if self.state != CANDIDATE or self.term != new_term:
                return
            if granted >= self.majority():
                self._event(f"won election at term {new_term} with votes from {', '.join(voters)}")
                self._become_leader()
            else:
                self._event(f"election at term {new_term} lost ({granted}/{self.cluster_size})")
                self.state = FOLLOWER
        finally:
            self._campaigning = False

    async def _ask_votes(self, term: int, pre: bool) -> tuple[int, list[str]]:
        msg = {"term": term, "candidate": self.id, "lastIndex": self.storage.last_index(),
               "lastTerm": self.storage.last_term(), "pre": pre}
        results = await asyncio.gather(*(self.transport(p, "vote", msg) for p in self.peers))
        granted, voters = 1, [self.id]
        for peer, resp in zip(self.peers, results):
            if not resp:
                continue
            if resp.get("granted"):
                granted += 1
                voters.append(peer)
            elif resp.get("term", 0) > self.term:
                self._become_follower(resp["term"], None)
        return granted, voters

    def handle_vote(self, msg: dict[str, Any]) -> dict[str, Any]:
        cand, term, pre = msg["candidate"], msg["term"], bool(msg.get("pre"))
        now = time.monotonic()
        kind = "pre-vote" if pre else "vote"
        if self.state == LEADER and pre:
            return {"term": self.term, "granted": False, "reason": "I am the leader"}
        if (self.leader_id not in (None, cand) and now - self.last_leader_contact < self.election_min):
            return {"term": self.term, "granted": False, "reason": f"leader {self.leader_id} is alive"}
        if term < self.term:
            return {"term": self.term, "granted": False, "reason": "stale term"}
        log_ok = self._log_up_to_date(msg["lastIndex"], msg["lastTerm"])
        if pre:
            granted = log_ok and term > self.term
            self.log.debug("%s request from %s for term %s -> %s", kind, cand, term, granted)
            return {"term": self.term, "granted": granted}
        if term > self.term:
            self._become_follower(term, None)
        if self.storage.voted_for in (None, cand) and log_ok:
            self.storage.set_term_and_vote(self.term, cand)
            self._reset_election_deadline()
            self._event(f"voted for {cand} at term {term}")
            return {"term": self.term, "granted": True}
        return {"term": self.term, "granted": False,
                "reason": "already voted" if self.storage.voted_for else "candidate log is behind"}

    # -- role changes --------------------------------------------------------

    def _set_leader(self, leader: str | None) -> None:
        if leader != self.leader_id:
            self.leader_id = leader
            if self.on_leader_change:
                self.on_leader_change(leader)

    def _become_follower(self, term: int, leader: str | None) -> None:
        if term > self.term:
            self.storage.set_term_and_vote(term, None)
        was = self.state
        self.state = FOLLOWER
        if was == LEADER:
            self._event(f"stepped down at term {self.term}")
            for task in self._replicators:
                task.cancel()
            self._replicators = []
        if leader != self.leader_id and leader is not None:
            self._event(f"following leader {leader} at term {self.term}")
        self._set_leader(leader)

    def _become_leader(self) -> None:
        self.state = LEADER
        self._set_leader(self.id)
        now = time.monotonic()
        self.became_leader_at = now
        last = self.storage.last_index()
        for p in self.peers:
            self.next_index[p] = last + 1
            self.match_index[p] = 0
            self.last_ack[p] = now
            self._kick[p] = asyncio.Event()
        self._event(f"became leader at term {self.term}")
        self.propose("")  # empty entry: lets us commit entries left by the previous leader
        self._replicators = [asyncio.create_task(self._replicate(p, self.term)) for p in self.peers]

    # -- replication (leader side) -------------------------------------------

    def propose(self, data: str) -> int:
        """Append an entry to the leader's log and start replicating it."""
        if self.state != LEADER:
            raise NotLeader(self.leader_id)
        idx = self.storage.last_index() + 1
        self.storage.append([[idx, self.term, data]])
        for ev in self._kick.values():
            ev.set()
        if self.cluster_size == 1:
            self._advance_commit()
        return idx

    async def _replicate(self, peer: str, term: int) -> None:
        backoff = self.heartbeat
        while self.state == LEADER and self.term == term:
            next_i = self.next_index[peer]
            prev_i = next_i - 1
            prev_t = self.storage.term_at(prev_i)
            if prev_t is None:  # our log shrank under us (shouldn't happen as leader)
                self.next_index[peer] = self.storage.last_index() + 1
                continue
            entries = self.storage.entries_from(next_i, MAX_ENTRIES_PER_APPEND)
            msg = {"term": term, "leader": self.id, "prevIndex": prev_i, "prevTerm": prev_t,
                   "entries": entries, "leaderCommit": self.commit_index}
            resp = await self.transport(peer, "append", msg)
            if self.state != LEADER or self.term != term:
                return
            if resp is None:
                await asyncio.sleep(min(backoff, 1.0))
                backoff = min(backoff * 2, 1.0)
                continue
            backoff = self.heartbeat
            if resp.get("term", 0) > self.term:
                self._event(f"saw higher term {resp['term']} from {peer}")
                self._become_follower(resp["term"], None)
                return
            self.last_ack[peer] = time.monotonic()
            if resp.get("success"):
                match = resp.get("matchIndex", prev_i + len(entries))
                if match > self.match_index[peer]:
                    self.match_index[peer] = match
                self.next_index[peer] = self.match_index[peer] + 1
                self._advance_commit()
            else:
                hint = resp.get("conflictIndex", prev_i)
                self.next_index[peer] = max(1, min(hint, next_i - 1))
                continue  # retry immediately with the earlier index
            if self.next_index[peer] <= self.storage.last_index():
                continue  # more to send
            kick = self._kick[peer]
            try:
                await asyncio.wait_for(kick.wait(), timeout=self.heartbeat)
            except asyncio.TimeoutError:
                pass
            kick.clear()

    def _advance_commit(self) -> None:
        for n in range(self.storage.last_index(), self.commit_index, -1):
            if self.storage.term_at(n) != self.term:
                break  # only entries from the current term are committed by counting
            replicas = 1 + sum(1 for p in self.peers if self.match_index.get(p, 0) >= n)
            if replicas >= self.majority():
                self.commit_index = n
                self._apply_committed()
                break

    # -- AppendEntries (follower side) ---------------------------------------

    def handle_append(self, msg: dict[str, Any]) -> dict[str, Any]:
        term, leader = msg["term"], msg["leader"]
        if term < self.term:
            return {"term": self.term, "success": False}
        if term > self.term or self.state != FOLLOWER or self.leader_id != leader:
            self._become_follower(term, leader)
        self.last_leader_contact = time.monotonic()
        self._reset_election_deadline()

        prev_i, prev_t = msg["prevIndex"], msg["prevTerm"]
        last = self.storage.last_index()
        if prev_i > last:
            return {"term": self.term, "success": False, "conflictIndex": last + 1}
        my_prev_t = self.storage.term_at(prev_i)
        if my_prev_t != prev_t:
            return {"term": self.term, "success": False,
                    "conflictIndex": self.storage.first_index_of_term(my_prev_t or 0)}

        entries = msg["entries"]
        for i, (idx, eterm, _data) in enumerate(entries):
            mine = self.storage.term_at(idx) if idx <= self.storage.last_index() else None
            if mine is None:
                self.storage.append(entries[i:])
                break
            if mine != eterm:
                if idx <= self.last_applied:
                    self.log.error("refusing to overwrite applied entry %s (Raft invariant broken)", idx)
                    return {"term": self.term, "success": False, "conflictIndex": self.last_applied + 1}
                self._event(f"discarding divergent log tail from index {idx} (term {mine} vs leader's {eterm})")
                self.storage.truncate_from(idx)
                self.storage.append(entries[i:])
                break
        match = prev_i + len(entries)
        if msg["leaderCommit"] > self.commit_index:
            self.commit_index = min(msg["leaderCommit"], match)
            self._apply_committed()
        return {"term": self.term, "success": True, "matchIndex": match}

    # -- applying committed entries ------------------------------------------

    def _apply_committed(self) -> None:
        applied_any = False
        while self.last_applied < self.commit_index:
            idx = self.last_applied + 1
            self.apply_fn(idx, self.storage.data_at(idx))
            self.last_applied = idx
            applied_any = True
        if applied_any:
            asyncio.get_running_loop().create_task(self._notify_applied())

    async def _notify_applied(self) -> None:
        async with self.applied:
            self.applied.notify_all()

    # -- introspection -------------------------------------------------------

    def status(self) -> dict[str, Any]:
        now = time.monotonic()
        peers = {}
        for p in self.peers:
            entry: dict[str, Any] = {}
            if self.state == LEADER:
                entry["matchIndex"] = self.match_index.get(p, 0)
                entry["nextIndex"] = self.next_index.get(p, 0)
                ack = self.last_ack.get(p)
                entry["lastAckMsAgo"] = int((now - ack) * 1000) if ack else None
                entry["reachable"] = bool(ack and now - ack < self.election_max)
            peers[p] = entry
        return {
            "id": self.id, "state": self.state, "term": self.term, "leader": self.leader_id,
            "votedFor": self.storage.voted_for, "commitIndex": self.commit_index,
            "lastApplied": self.last_applied, "lastLogIndex": self.storage.last_index(),
            "lastLogTerm": self.storage.last_term(), "peers": peers,
            "msSinceLeaderContact": int((now - self.last_leader_contact) * 1000) if self.last_leader_contact else None,
            "events": list(self.events)[-15:],
        }
