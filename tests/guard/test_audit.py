"""The audit log reports the first record that was altered, removed, reordered or forged."""

from __future__ import annotations

import hashlib
import json
from datetime import timedelta
from pathlib import Path

import pytest

from goldenhour.guard import keys
from goldenhour.guard.audit import GENESIS, AuditLog, verify

from .helpers import NOW

KEY = keys.from_seed(b"g" * 32)
PUBLIC = KEY.public_key()


def write_log(path: Path, count: int = 4) -> list[dict[str, object]]:
    log = AuditLog(path, KEY)
    return [
        log.append({"type": "decision", "n": i, "note": "ünïcode"}, NOW + timedelta(seconds=i)) for i in range(count)
    ]


def lines(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8").splitlines(keepends=True)


def test_a_clean_log_verifies(tmp_path: Path) -> None:
    path = tmp_path / "audit.jsonl"
    records = write_log(path)
    report = verify(path, PUBLIC)
    assert report.ok and report.records == 4 and report.head == records[-1]["hash"]
    assert records[0]["prev_hash"] == GENESIS
    assert [r["seq"] for r in records] == [0, 1, 2, 3]
    assert all(records[i]["prev_hash"] == records[i - 1]["hash"] for i in range(1, 4))
    assert verify(path, PUBLIC, expected_head="0x" + str(records[-1]["hash"])).ok


def test_reopening_continues_the_chain(tmp_path: Path) -> None:
    path = tmp_path / "audit.jsonl"
    first = write_log(path, 2)
    log = AuditLog(path, KEY)
    assert log.head == first[-1]["hash"]
    record = log.append({"type": "decision"}, NOW)
    assert (record["seq"], record["prev_hash"]) == (2, first[-1]["hash"])
    assert verify(path, PUBLIC).records == 3


def test_altered_content_is_reported(tmp_path: Path) -> None:
    path = tmp_path / "audit.jsonl"
    write_log(path)
    content = lines(path)
    content[2] = content[2].replace('"n":2', '"n":20')
    path.write_text("".join(content), encoding="utf-8")
    report = verify(path, PUBLIC)
    assert not report.ok and report.line == 3 and report.records == 2
    assert report.reason == "record 2 was altered: its content no longer matches its hash"


def test_a_removed_record_is_reported(tmp_path: Path) -> None:
    path = tmp_path / "audit.jsonl"
    write_log(path)
    content = lines(path)
    del content[1]
    path.write_text("".join(content), encoding="utf-8")
    report = verify(path, PUBLIC)
    assert report.line == 2 and report.reason == "record 1 is missing: the next record found is 2"


def test_reordered_records_are_reported(tmp_path: Path) -> None:
    path = tmp_path / "audit.jsonl"
    write_log(path)
    content = lines(path)
    content[1], content[2] = content[2], content[1]
    path.write_text("".join(content), encoding="utf-8")
    assert verify(path, PUBLIC).reason == "record 1 is missing: the next record found is 2"
    content[0], content[1] = content[1], content[0]
    path.write_text("".join(content), encoding="utf-8")
    assert verify(path, PUBLIC).reason.startswith("record 0 is missing")


def test_a_replaced_record_that_relinks_is_reported(tmp_path: Path) -> None:
    """Rewriting a record and recomputing its hash still fails: the next link and the signature break."""
    path = tmp_path / "audit.jsonl"
    write_log(path)
    content = lines(path)
    record = json.loads(content[1])
    record["event"]["n"] = 99
    body = {k: record[k] for k in ("seq", "at", "prev_hash", "event")}
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    record["hash"] = hashlib.sha256(canonical).hexdigest()
    forger = keys.from_seed(b"f" * 32)
    record["signature"] = forger.sign(bytes.fromhex(record["hash"])).hex()
    content[1] = json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n"
    path.write_text("".join(content), encoding="utf-8")
    report = verify(path, PUBLIC)
    assert report.line == 2 and report.reason == "record 1 is not signed by the guard's key"


def test_truncation_needs_the_published_head(tmp_path: Path) -> None:
    path = tmp_path / "audit.jsonl"
    records = write_log(path)
    path.write_text("".join(lines(path)[:2]), encoding="utf-8")
    assert verify(path, PUBLIC).ok  # a shorter chain is still a valid chain
    report = verify(path, PUBLIC, expected_head=str(records[-1]["hash"]))
    assert not report.ok and report.line is None and "is not in the log" in report.reason


@pytest.mark.parametrize(
    ("damage", "reason"),
    [
        (lambda line: "not json\n", "is not valid JSON"),
        (lambda line: json.dumps({**json.loads(line), "extra": 1}) + "\n", "missing or unexpected fields"),
        (lambda line: json.dumps({**json.loads(line), "signature": "zz"}) + "\n", "malformed signature"),
    ],
)
def test_damaged_lines(tmp_path: Path, damage: object, reason: str) -> None:
    path = tmp_path / "audit.jsonl"
    write_log(path)
    content = lines(path)
    content[3] = damage(content[3])  # type: ignore[operator]
    path.write_text("".join(content), encoding="utf-8")
    report = verify(path, PUBLIC)
    assert report.line == 4 and reason in report.reason


def test_refuses_to_append_to_a_damaged_log(tmp_path: Path) -> None:
    path = tmp_path / "audit.jsonl"
    write_log(path, 2)
    content = lines(path)
    content[-1] = content[-1].replace('"n":1', '"n":7')
    path.write_text("".join(content), encoding="utf-8")
    with pytest.raises(ValueError, match="damaged"):
        AuditLog(path, KEY)


def test_long_logs_are_resumed_from_the_tail(tmp_path: Path) -> None:
    path = tmp_path / "audit.jsonl"
    log = AuditLog(path, KEY)
    for index in range(60):
        log.append({"type": "decision", "padding": "x" * 400, "n": index}, NOW)
    assert AuditLog(path, KEY).head == log.head
    assert verify(path, PUBLIC).records == 60


def test_a_replayed_record_is_reported(tmp_path: Path) -> None:
    path = tmp_path / "audit.jsonl"
    write_log(path, 2)
    content = lines(path)
    path.write_text("".join([*content, content[1]]), encoding="utf-8")
    report = verify(path, PUBLIC)
    assert report.line == 3 and report.reason == "record 2 is out of order: found record 1 in its place"


def test_refuses_to_append_after_a_torn_write(tmp_path: Path) -> None:
    path = tmp_path / "audit.jsonl"
    write_log(path, 1)
    with path.open("a", encoding="utf-8") as handle:
        handle.write('{"seq": 1, "at": "2026')
    with pytest.raises(ValueError, match="damaged"):
        AuditLog(path, KEY)


def test_a_head_published_earlier_is_found_anywhere_in_the_chain(tmp_path: Path) -> None:
    path = tmp_path / "audit.jsonl"
    records = write_log(path)
    published = str(records[1]["hash"])
    report = verify(path, PUBLIC, expected_head=published)
    assert report.ok and report.after_expected == 2
    # Truncating the log to before the published head is caught, though the shorter chain is valid.
    path.write_text("".join(lines(path)[:1]), encoding="utf-8")
    assert verify(path, PUBLIC).ok
    truncated = verify(path, PUBLIC, expected_head=published)
    assert not truncated.ok and "is not in the log" in truncated.reason


def test_two_writers_extend_one_chain_instead_of_forking_it(tmp_path: Path) -> None:
    path = tmp_path / "audit.jsonl"
    first, second = AuditLog(path, KEY), AuditLog(path, KEY)  # both opened on an empty log
    for index in range(3):
        first.append({"type": "decision", "writer": "first", "n": index}, NOW)
        second.append({"type": "decision", "writer": "second", "n": index}, NOW)
    report = verify(path, PUBLIC)
    assert report.ok and report.records == 6
    assert second.head == report.head


def test_a_writer_refuses_a_log_it_did_not_sign(tmp_path: Path) -> None:
    path = tmp_path / "audit.jsonl"
    AuditLog(path, keys.from_seed(b"o" * 32)).append({"type": "decision"}, NOW)
    with pytest.raises(ValueError, match="not signed by this guard's key"):
        AuditLog(path, KEY)
