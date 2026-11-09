"""The guard_app commands, run the way an operator runs them."""

from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest
import yaml
from click.testing import Result
from typer.testing import CliRunner

from goldenhour.guard import keys
from goldenhour.guard.cases import demo_approver
from goldenhour.guard.cli import guard_app

from .helpers import CUSTOMER, ETH, HOT, VAULT, base_policy, call, hexed, tx

NOW = "2026-11-01T12:00:00Z"


def run(*args: object) -> Result:
    return CliRunner().invoke(guard_app, [str(a) for a in args])


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    (tmp_path / "policy.yaml").write_text(yaml.safe_dump(base_policy()))
    (tmp_path / "intent.json").write_text(
        json.dumps(
            {
                "id": "wd-1",
                "wallet": HOT,
                "chain_id": 1,
                "asset": "native",
                "destination": CUSTOMER,
                "amount": str(ETH),
                "expires_at": "2026-11-01T13:00:00Z",
            }
        )
    )
    (tmp_path / "withdrawal.json").write_text(json.dumps(tx(value=ETH, intent_id="wd-1").to_json()))
    (tmp_path / "held.json").write_text(json.dumps(tx(to=VAULT, data=hexed(call("deposit(uint256)", 1))).to_json()))
    return tmp_path


def test_keygen_writes_a_private_key_and_prints_the_public_one(tmp_path: Path) -> None:
    key_path = tmp_path / "guard.pem"
    result = run("keygen", key_path)
    assert result.exit_code == 0
    public = result.output.strip()
    assert keys.public_key_hex(keys.load_private_key(key_path)) == public
    assert (tmp_path / "guard.pem.pub").read_text().strip() == public
    assert stat.S_IMODE(key_path.stat().st_mode) == 0o600
    again = run("keygen", key_path)
    assert again.exit_code == 2 and "never overwrites" in again.output


def test_check_allows_once_then_denies(workspace: Path) -> None:
    ledger, log, key = workspace / "ledger.db", workspace / "audit.jsonl", workspace / "guard.pem"
    assert run("keygen", key).exit_code == 0
    added = run("add-intent", workspace / "intent.json", "--ledger", ledger)
    assert added.exit_code == 0 and "added 1 intent" in added.output
    common = ("--policy", workspace / "policy.yaml", "--ledger", ledger, "--log", log, "--key", key, "--now", NOW)
    first = run("check", workspace / "withdrawal.json", *common, "--json")
    assert first.exit_code == 0
    body = json.loads(first.output)
    assert body["decision"] == "allow" and body["intent"] == "wd-1" and body["audit_record"]
    second = run("check", workspace / "withdrawal.json", *common)
    assert second.exit_code == 4
    assert "DENY" in second.output and "already used" in second.output
    verified = run("verify-log", log, "--public-key", workspace / "guard.pem.pub")
    assert verified.exit_code == 0 and "OK 2 records" in verified.output
    duplicate = run("add-intent", workspace / "intent.json", "--ledger", ledger)
    assert duplicate.exit_code == 2 and "already exists" in duplicate.output


def test_hold_then_approve_then_allow(workspace: Path) -> None:
    policy = ("--policy", workspace / "policy.yaml", "--now", NOW)
    held = run("check", workspace / "held.json", *policy)
    assert held.exit_code == 3 and "HOLD" in held.output
    for name in ("alice", "bob"):
        key_path = workspace / f"{name}.pem"
        keys.save_private_key(demo_approver(name), key_path)
        result = run("approve", workspace / "held.json", "--key", key_path, "--approver", name, "--write")
        assert result.exit_code == 0 and json.loads(result.output)["approver"] == name
    assert len(json.loads((workspace / "held.json").read_text())["approvals"]) == 2
    assert run("check", workspace / "held.json", *policy).exit_code == 0


def test_an_unreadable_request_is_denied_and_logged(workspace: Path) -> None:
    key = workspace / "guard.pem"
    run("keygen", key)
    (workspace / "bad.json").write_text(json.dumps({"kind": "evm_tx", "chain_id": 1, "wallet": HOT}))
    log = workspace / "audit.jsonl"
    result = run("check", workspace / "bad.json", "-p", workspace / "policy.yaml", "--log", log, "--key", key, "--json")
    assert result.exit_code == 4
    assert json.loads(result.output)["reasons"][0]["rule"] == "well-formed"
    record = json.loads(log.read_text())
    assert record["event"]["type"] == "unreadable"
    assert run("verify-log", log, "--public-key", (workspace / "guard.pem.pub").read_text()).exit_code == 0


def test_usage_errors_exit_2(workspace: Path) -> None:
    request, policy = workspace / "withdrawal.json", workspace / "policy.yaml"
    assert "--log needs --key" in run("check", request, "-p", policy, "--log", workspace / "a.jsonl").output
    assert run("check", request, "-p", policy, "--now", "yesterday").exit_code == 2
    assert run("check", request, "-p", policy, "--now", "2026-11-01T12:00:00").exit_code == 2
    (workspace / "broken.yaml").write_text("version: 1\nname: x\n")
    broken = run("check", request, "-p", workspace / "broken.yaml")
    assert broken.exit_code == 2 and "not a valid policy" in broken.output
    assert run("verify-log", workspace / "intent.json", "--public-key", "abcd").exit_code == 2


def test_verify_log_reports_tampering(workspace: Path) -> None:
    key, log = workspace / "guard.pem", workspace / "audit.jsonl"
    run("keygen", key)
    for _ in range(2):
        run("check", workspace / "held.json", "-p", workspace / "policy.yaml", "--log", log, "--key", key, "--now", NOW)
    log.write_text(log.read_text().replace('"decision":"hold"', '"decision":"allow"', 1))
    result = run("verify-log", log, "--public-key", workspace / "guard.pem.pub")
    assert result.exit_code == 1
    assert "BROKEN (line 1): record 0 was altered" in result.output


def test_request_text_cannot_style_the_output(workspace: Path) -> None:
    sneaky = {"kind": "evm_message", "chain_id": 1, "wallet": HOT, "message": hexed(b"[white on white]hidden[/] ok")}
    (workspace / "sneaky.json").write_text(json.dumps(sneaky))
    result = run("check", workspace / "sneaky.json", "-p", workspace / "policy.yaml", "--now", NOW)
    assert result.exit_code == 3
    # Interpreted as markup, the tag would vanish from the output and hide the word after it.
    assert "[white" in result.output and "[/]" in result.output


def test_a_request_that_would_crash_a_lenient_parser_is_denied_and_logged(workspace: Path) -> None:
    from .test_primitives import MAIL

    key, log = workspace / "guard.pem", workspace / "audit.jsonl"
    run("keygen", key)
    types = {**MAIL["types"], "Mail": [*MAIL["types"]["Mail"], {"name": "amount", "type": "uint999"}]}
    body = {
        "kind": "evm_typed_data",
        "chain_id": 1,
        "wallet": HOT,
        "typed_data": {**MAIL, "types": types, "message": {**MAIL["message"], "amount": 2**300}},
    }
    (workspace / "huge.json").write_text(json.dumps(body))
    result = run(
        "check", workspace / "huge.json", "-p", workspace / "policy.yaml", "--log", log, "--key", key, "--json"
    )
    assert result.exit_code == 4
    assert json.loads(result.output)["reasons"][0]["rule"] == "well-formed"
    assert json.loads(log.read_text())["event"]["type"] == "unreadable"


def test_verify_log_accepts_a_head_published_before_later_records(workspace: Path) -> None:
    key, log = workspace / "guard.pem", workspace / "audit.jsonl"
    run("keygen", key)
    heads = []
    for _ in range(3):
        result = run(
            "check",
            workspace / "held.json",
            "-p",
            workspace / "policy.yaml",
            "--log",
            log,
            "--key",
            key,
            "--now",
            NOW,
            "--json",
        )
        heads.append(json.loads(result.output)["audit_record"])
    report = run("verify-log", log, "--public-key", workspace / "guard.pem.pub", "--head", heads[0])
    assert report.exit_code == 0 and "with 2 records after it" in report.output
    log.write_text(log.read_text().splitlines(keepends=True)[0])
    assert run("verify-log", log, "--public-key", workspace / "guard.pem.pub", "--head", heads[1]).exit_code == 1
