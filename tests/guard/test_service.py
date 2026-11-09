"""evaluate_json, the function the self-hosted server calls for POST /v1/guard/evaluate."""

from __future__ import annotations

import json
import stat
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from goldenhour.guard import keys
from goldenhour.guard.audit import verify
from goldenhour.guard.cli import guard_app
from goldenhour.guard.ledger import SqliteLedger
from goldenhour.guard.service import evaluate_json

from .helpers import ETH, base_policy, intent, tx

SEED = "11" * 32


@pytest.fixture
def state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A state directory and a test policy, with one live withdrawal intent in the ledger."""
    policy = tmp_path / "policy.yaml"
    policy.write_text(yaml.safe_dump(base_policy()))
    directory = tmp_path / "state"
    directory.mkdir()
    ledger = SqliteLedger(directory / "guard-ledger.db")
    ledger.add_intent(intent(expires_at=datetime.now(UTC) + timedelta(hours=1)))
    ledger.close()
    monkeypatch.setenv("GOLDENHOUR_STATE", str(directory))
    monkeypatch.setenv("GOLDENHOUR_GUARD_POLICY", str(policy))
    monkeypatch.delenv("GOLDENHOUR_GUARD_KEY", raising=False)
    return directory


def withdrawal() -> dict[str, object]:
    return tx(value=ETH, intent_id="wd-1").to_json()


def test_decides_commits_and_logs_with_a_key_created_on_first_use(state: Path) -> None:
    first = evaluate_json(withdrawal())
    assert first["decision"] == "allow" and first["intent"] == "wd-1"
    key_path = state / "guard-key.pem"
    assert stat.S_IMODE(key_path.stat().st_mode) == 0o600
    public = keys.parse_public_key((state / "guard-key.pem.pub").read_text())
    second = evaluate_json(withdrawal())
    assert second["decision"] == "deny" and "already used" in second["reasons"][0]["message"]
    report = verify(state / "guard-audit.jsonl", public, expected_head=first["audit_record"])
    assert report.ok and report.records == 2 and report.after_expected == 1
    assert report.head == second["audit_record"]


def test_a_bad_request_is_denied_and_logged_never_raised(state: Path) -> None:
    for body in ({"kind": "evm_tx"}, {"kind": "evm_message", "chain_id": 1, "wallet": "0x12", "message": "0x"}, {}):
        result = evaluate_json(body)
        assert result["decision"] == "deny"
        assert result["reasons"][0]["rule"] == "well-formed"
    records = [json.loads(line) for line in (state / "guard-audit.jsonl").read_text().splitlines()]
    assert [r["event"]["type"] for r in records] == ["unreadable"] * 3


def test_a_seed_from_the_environment_signs_the_log(state: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GOLDENHOUR_GUARD_KEY", "0x" + SEED)
    evaluate_json(withdrawal())
    public = keys.from_seed(bytes.fromhex(SEED)).public_key()
    assert verify(state / "guard-audit.jsonl", public).ok
    assert not (state / "guard-key.pem").exists()
    monkeypatch.setenv("GOLDENHOUR_GUARD_KEY", "abcd")
    with pytest.raises(ValueError, match="32-byte Ed25519 seed"):
        evaluate_json(withdrawal())


def test_the_shipped_default_policy_is_used_when_none_is_configured(
    state: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("GOLDENHOUR_GUARD_POLICY")
    result = evaluate_json(withdrawal())
    assert result["decision"] == "deny"
    assert result["reasons"][0]["rule"] == "known-wallet"  # the default policy lists no wallets


def test_returns_what_the_cli_prints(state: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GOLDENHOUR_GUARD_KEY", SEED)
    request = tmp_path / "request.json"
    held = tx(to="0x0000000000000000000000000000000000000ba5", data="0xb6b55f25" + "00" * 32).to_json()
    request.write_text(json.dumps(held))
    printed = CliRunner().invoke(guard_app, ["check", str(request), "-p", str(tmp_path / "policy.yaml"), "--json"])
    from_cli = json.loads(printed.output)
    from_service = evaluate_json(held)
    assert set(from_cli) == set(from_service)
    for volatile in ("evaluated_at", "audit_record"):
        from_cli.pop(volatile)
        from_service.pop(volatile)
    assert from_cli == from_service
