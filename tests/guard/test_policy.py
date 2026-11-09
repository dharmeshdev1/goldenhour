"""Loading policies: the shipped files, and the mistakes the loader refuses to paper over."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from goldenhour.guard.cases import COUNTERPARTY, CUSTOMER, HOT, TREASURY, demo_address, demo_approver
from goldenhour.guard.keys import public_key_hex
from goldenhour.guard.policy import RULE_IDS, load_policy

from .helpers import base_policy, make_policy

POLICIES = Path(__file__).resolve().parents[2] / "policies"


def test_default_policy_loads_and_fails_closed() -> None:
    policy = load_policy(POLICIES / "default.yaml")
    assert policy.wallets == []
    assert policy.quorum.approvers == []
    assert policy.rules.no_delegatecall.allow_targets[0].label == "Safe MultiSendCallOnly 1.3.0"
    assert policy.rules.no_unlimited_approval.threshold == 2**96 - 1
    assert policy.rules.velocity.window == 86400


def test_demo_policy_matches_the_default_rules_and_the_demo_seeds() -> None:
    default = load_policy(POLICIES / "default.yaml")
    demo = load_policy(POLICIES / "demo.yaml")
    for rule_id in RULE_IDS[2:-1]:
        assert demo.rules.get(rule_id).severity == default.rules.get(rule_id).severity, rule_id
        assert demo.rules.get(rule_id).enabled
    assert demo.assets == default.assets
    assert {w.address for w in demo.wallets} >= {HOT, TREASURY}
    assert demo.rules.destination_allowlist.destinations[0].address == demo_address("cold storage")
    assert [a.public_key for a in demo.quorum.approvers] == [
        public_key_hex(demo_approver(name)) for name in ("alice", "bob", "carol")
    ]
    assert CUSTOMER not in {w.address for w in demo.wallets} and COUNTERPARTY != CUSTOMER


def _broken(path: tuple[str, ...], value: Any) -> dict[str, Any]:
    data = base_policy()
    target = data
    for key in path[:-1]:
        target = target[key]
    if value is _DELETE:
        del target[path[-1]]
    else:
        target[path[-1]] = value
    return data


_DELETE = object()


@pytest.mark.parametrize(
    ("path", "value", "reason"),
    [
        (("rules", "velocity"), _DELETE, "velocity"),
        (("rules", "no-such-rule"), {}, "Extra inputs"),
        (("rules", "max-per-tx", "limit"), 5, "Extra inputs"),
        (("rules", "max-per-tx", "severity"), "warn", "deny"),
        (("wallets", 0, "address"), 0x1234, "quote addresses"),
        (("wallets", 0, "address"), "0x66A2f4A63310aCa888F8e578BA5a7093BA3Bb8B3", "checksum"),
        (("quorum", "threshold"), 4, "more than the 3 approvers"),
        (("quorum", "threshold"), 0, "greater than or equal to 1"),
        (("rules", "velocity", "window"), "1w", "duration"),
        (("rules", "velocity", "window"), "0h", "duration"),
        (("rules", "explainable", "contracts"), [{"address": HOT, "functions": ["nope"]}], "selector"),
        (
            ("rules", "no-upgrade-or-owner-change", "change_windows"),
            [{"start": "2026-11-02T00:00:00Z", "end": "2026-11-01T00:00:00Z"}],
            "end after it starts",
        ),
        (
            ("rules", "no-upgrade-or-owner-change", "change_windows"),
            [{"start": "2026-11-01T00:00:00", "end": "2026-11-02T00:00:00"}],
            "timezone",
        ),
        (("assets", 0, "decimals"), 78, "less than or equal to 77"),
        (("version",), 2, "Input should be 1"),
    ],
)
def test_mistakes_are_errors_not_defaults(path: tuple[Any, ...], value: Any, reason: str) -> None:
    with pytest.raises(ValidationError, match=reason):
        load_policy(yaml.safe_dump(_broken(path, value)))


def test_duplicates_are_refused() -> None:
    data = base_policy()
    data["wallets"].append(dict(data["wallets"][0]))
    with pytest.raises(ValidationError, match="listed twice"):
        load_policy(yaml.safe_dump(data))
    data = base_policy()
    data["quorum"]["approvers"][1]["public_key"] = data["quorum"]["approvers"][0]["public_key"]
    with pytest.raises(ValidationError, match="their own key"):
        load_policy(yaml.safe_dump(data))
    data = base_policy()
    data["quorum"]["approvers"][1]["id"] = "alice"
    with pytest.raises(ValidationError, match="unique"):
        load_policy(yaml.safe_dump(data))


def test_unquoted_hex_key_in_yaml_is_caught() -> None:
    data = yaml.safe_dump(base_policy()).replace(base_policy()["quorum"]["approvers"][0]["public_key"], "12345")
    with pytest.raises(ValidationError, match="quote it"):
        load_policy(data)


def test_durations_selectors_and_lookups() -> None:
    policy = make_policy(
        {
            "rules": {
                "velocity": {"window": "15m"},
                "explainable": {
                    "contracts": [{"address": COUNTERPARTY, "functions": ["deposit(uint256)", "0xABCDEF01"]}]
                },
            }
        }
    )
    assert policy.rules.velocity.window == 900
    assert policy.rules.explainable.contracts[0].functions == ["0xb6b55f25", "0xabcdef01"]
    assert make_policy({"rules": {"velocity": {"window": 3600}}}).rules.velocity.window == 3600
    assert make_policy({"rules": {"velocity": {"window": "7d"}}}).rules.velocity.window == 7 * 86400
    assert policy.label(HOT) == "hot"
    assert policy.label("0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48") == "USDC"
    assert policy.label("0x0000000000000000000000000000000000007007") == "router"
    assert policy.label(COUNTERPARTY) == ""
    assert policy.asset(1, "native").symbol == "ETH"
    assert policy.asset(10, "native") is None


def test_policy_digest_ignores_comments_and_key_order() -> None:
    text = yaml.safe_dump(base_policy(), sort_keys=True)
    reordered = "# a comment\n" + yaml.safe_dump(base_policy(), sort_keys=False)
    assert load_policy(text).digest == load_policy(reordered).digest
    assert make_policy({"rules": {"velocity": {"limit_usd": 1}}}).digest != load_policy(text).digest


@pytest.mark.parametrize(
    "addition",
    [
        "quorum:\n  threshold: 1\n",
        "rules:\n  velocity:\n    severity: hold\n    window: 1h\n    limit_usd: 1\n",
    ],
)
def test_a_repeated_key_is_refused_not_silently_kept(addition: str) -> None:
    text = yaml.safe_dump(base_policy(), sort_keys=False) + addition
    with pytest.raises(ValueError, match="appears twice"):
        load_policy(text)


def test_a_repeated_key_inside_a_rule_is_refused() -> None:
    text = yaml.safe_dump(base_policy(), sort_keys=False).replace(
        "  max-per-tx:\n    severity: hold\n", "  max-per-tx:\n    severity: hold\n    severity: hold\n", 1
    )
    with pytest.raises(ValueError, match="'severity' appears twice"):
        load_policy(text)
    with pytest.raises(ValueError, match="not valid YAML"):
        load_policy("rules: [unclosed")
