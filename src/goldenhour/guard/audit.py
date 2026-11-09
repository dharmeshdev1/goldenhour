"""The audit log: every decision Guard makes, append-only, hash-chained and signed.

Each JSONL record carries a sequence number, the hash of the record before it (`prev_hash`), its
own hash over its content, and the guard's Ed25519 signature of that hash. Editing a record breaks
its hash, deleting one breaks the next record's `prev_hash` and the sequence, and forging a
replacement needs the guard's key. Hashes are SHA-256 so the chain can be checked with standard
tools.

Removing records from the end leaves a shorter chain that is still valid. To catch that, publish
the head (the newest hash) somewhere else from time to time: `verify` accepts any head published
earlier, finds it in the chain and reports how many records came after it, and fails if it is gone.

Appends take an exclusive lock on the file and re-read its last record under the lock, so two
processes writing one log extend a single chain instead of forking it. A writer refuses to extend
a log whose last record is damaged or was not signed by its own key. The lock is a POSIX advisory
lock (`fcntl.flock`).
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import IO, Any

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from goldenhour.guard.keys import verify as verify_signature

GENESIS = "0" * 64
_FIELDS = ("seq", "at", "prev_hash", "event")


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _record_hash(record: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical({name: record[name] for name in _FIELDS})).hexdigest()


def _last_line(handle: IO[bytes]) -> bytes:
    """The last non-empty line, read backwards so a long log is not read in full."""
    handle.seek(0, os.SEEK_END)
    position = handle.tell()
    tail = b""
    while position > 0:
        step = min(8192, position)
        position -= step
        handle.seek(position)
        tail = handle.read(step) + tail
        lines = tail.rstrip(b"\n").split(b"\n")
        if len(lines) > 1 or position == 0:
            return lines[-1]
    return b""


class AuditLog:
    def __init__(self, path: Path, key: Ed25519PrivateKey) -> None:
        self.path = path
        self._key = key
        self._head = GENESIS
        if path.exists():
            with path.open("rb") as handle:
                _, self._head = self._tail(handle)

    @property
    def head(self) -> str:
        """Hash of the newest record this writer has seen. Publish it elsewhere to make truncation detectable."""
        return self._head

    def _tail(self, handle: IO[bytes]) -> tuple[int, str]:
        """The next sequence number and the head, after checking the last record is intact and ours."""
        last = _last_line(handle)
        if not last:
            return 0, GENESIS
        try:
            record = json.loads(last)
            intact = _record_hash(record) == record["hash"] and verify_signature(
                self._key.public_key(), bytes.fromhex(record["signature"]), bytes.fromhex(record["hash"])
            )
        except (ValueError, KeyError, TypeError):
            intact = False
        if not intact:
            raise ValueError(
                f"the last record of {self.path} is damaged or not signed by this guard's key; "
                "run verify-log before appending"
            )
        return record["seq"] + 1, record["hash"]

    def append(self, event: dict[str, Any], at: datetime) -> dict[str, Any]:
        with self.path.open("a+b") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)  # released when the file closes
            seq, head = self._tail(handle)
            record: dict[str, Any] = {
                "seq": seq,
                "at": at.isoformat().replace("+00:00", "Z"),
                "prev_hash": head,
                "event": event,
            }
            record["hash"] = _record_hash(record)
            record["signature"] = self._key.sign(bytes.fromhex(record["hash"])).hex()
            handle.write(_canonical(record) + b"\n")
            handle.flush()
            os.fsync(handle.fileno())
        self._head = record["hash"]
        return record


@dataclass(frozen=True)
class VerifyReport:
    ok: bool
    records: int  # records that verified before the first problem
    head: str  # hash of the last record that verified
    line: int | None = None  # 1-based line of the first bad record
    reason: str = ""
    after_expected: int | None = None  # records written after the expected head, when it was found


def _problem(line: str, seq: int, previous: str, public_key: Ed25519PublicKey) -> str | None:
    """Why the record on `line` cannot be record `seq` of this chain, or None if it is."""
    try:
        record = json.loads(line)
    except json.JSONDecodeError:
        return "is not valid JSON"
    if not isinstance(record, dict) or set(record) != {*_FIELDS, "hash", "signature"}:
        return "has missing or unexpected fields"
    if record["seq"] != seq:
        if isinstance(record["seq"], int) and record["seq"] > seq:
            return f"is missing: the next record found is {record['seq']}"
        return f"is out of order: found record {record['seq']} in its place"
    if record["prev_hash"] != previous:
        return "does not link to the record before it, so a record was replaced or removed"
    if _record_hash(record) != record["hash"]:
        return "was altered: its content no longer matches its hash"
    try:
        signature, digest = bytes.fromhex(record["signature"]), bytes.fromhex(record["hash"])
    except (TypeError, ValueError):
        return "has a malformed signature"
    if not verify_signature(public_key, signature, digest):
        return "is not signed by the guard's key"
    return None


def verify(path: Path, public_key: Ed25519PublicKey, expected_head: str | None = None) -> VerifyReport:
    """Check every record in order and report the first one that was altered, removed or forged.

    `expected_head` is a hash published earlier. It may be any record's hash, since the log keeps
    growing after it is published; if no record has it, records were removed or rewritten.
    """
    wanted = expected_head.removeprefix("0x").lower() if expected_head is not None else None
    count, previous, found = 0, GENESIS, None
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            problem = _problem(line, count, previous, public_key)
            if problem is not None:
                return VerifyReport(False, count, previous, line_number, f"record {count} {problem}")
            count, previous = count + 1, json.loads(line)["hash"]
            if previous == wanted:
                found = count
    if wanted is None:
        return VerifyReport(True, count, previous)
    if found is None:
        reason = f"the published head {wanted} is not in the log, so records were removed or rewritten"
        return VerifyReport(False, count, previous, None, reason)
    return VerifyReport(True, count, previous, after_expected=count - found)
