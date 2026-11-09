"""The policy: which wallets Guard protects, how it values assets, its rules, and who may approve.

A policy is a YAML file that reviewers can read and diff. Loading is strict on purpose: an unknown
key, a missing rule or an unquoted address is an error, never a silent default, because a typo that
quietly switched a rule off would be invisible until the day it mattered. Every rule must appear
in the file, with `enabled: false` if an operator really means to turn it off.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Annotated, Literal

import yaml
from pydantic import (
    AwareDatetime,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    PlainSerializer,
    PrivateAttr,
    model_validator,
)

from goldenhour.guard.evm import normalize_asset, selector
from goldenhour.guard.keys import parse_public_key
from goldenhour.guard.request import Address

type Severity = Literal["deny", "hold"]

# Fixed order: findings are reported in this order, so the same request always reads the same way.
RULE_IDS = (
    "known-wallet",
    "well-formed",
    "no-eip7702-delegation",
    "no-delegatecall",
    "no-blind-signing",
    "no-upgrade-or-owner-change",
    "no-unlimited-approval",
    "approval-allowlist",
    "intent-required",
    "velocity",
    "max-per-tx",
    "destination-allowlist",
    "explainable",
    "quorum",
)

POLICIES_DIR = Path(__file__).resolve().parents[3] / "policies"
if not POLICIES_DIR.exists():  # installed wheel
    POLICIES_DIR = Path(__file__).resolve().parents[1] / "_policies"
DEFAULT_POLICY = POLICIES_DIR / "default.yaml"

_DURATION = re.compile(r"(\d+)([smhd])")
_UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400}
_SELECTOR = re.compile(r"0x[0-9a-fA-F]{8}")


def _duration(value: object) -> int:
    """Seconds, from a number or a string such as `90s`, `15m`, `24h` or `7d`."""
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return value
    match = _DURATION.fullmatch(str(value).strip())
    if not match or int(match.group(1)) == 0:
        raise ValueError(f"expected a duration such as 24h, got {value!r}")
    return int(match.group(1)) * _UNITS[match.group(2)]


def _selector(value: object) -> str:
    """A selector from `0x` and 8 hex digits, or from a signature such as `deposit(uint256)`."""
    text = str(value).strip()
    if _SELECTOR.fullmatch(text):
        return text.lower()
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*\([^()\s]*\)", text):
        return "0x" + selector(text).hex()
    raise ValueError(f"expected a 4-byte selector or a function signature, got {value!r}")


def _public_key(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("an approver public key is 64 hex digits; quote it in YAML")
    parse_public_key(value)
    return value.strip().removeprefix("0x").lower()


Duration = Annotated[int, BeforeValidator(_duration)]
Selector = Annotated[str, BeforeValidator(_selector)]
AssetId = Annotated[str, BeforeValidator(normalize_asset)]
Usd = Annotated[Decimal, Field(ge=0), PlainSerializer(str, when_used="json")]
PublicKey = Annotated[str, BeforeValidator(_public_key)]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Wallet(_Model):
    """A custody wallet Guard protects. Limits set here override the rule defaults for this wallet."""

    address: Address
    label: str = Field(min_length=1)
    role: str = "hot"
    max_per_tx_usd: Usd | None = None
    velocity_limit_usd: Usd | None = None


class Asset(_Model):
    """An asset Guard can value. The floor stops a caller from pricing an asset near zero to slip past
    the USD limits: Guard values the asset at the higher of the caller's price and the floor."""

    chain_id: int = Field(ge=1)
    address: AssetId
    symbol: str = Field(min_length=1)
    decimals: int = Field(ge=0, le=77)
    price_floor_usd: Usd = Decimal(0)


class Entry(_Model):
    """An allowlist entry: an address with an optional label. A bare address string is accepted."""

    address: Address
    label: str = ""

    @model_validator(mode="before")
    @classmethod
    def _bare(cls, value: object) -> object:
        return {"address": value} if isinstance(value, str) else value


class Contract(Entry):
    """A contract Guard may call without decoding. `functions` limits which; empty means all of them."""

    functions: list[Selector] = Field(default_factory=list)


class ChangeWindow(_Model):
    start: AwareDatetime
    end: AwareDatetime
    reason: str = ""

    @model_validator(mode="after")
    def _ordered(self) -> ChangeWindow:
        if self.end <= self.start:
            raise ValueError("a change window must end after it starts")
        return self


class Rule(_Model):
    enabled: bool = True
    severity: Severity


class DelegationRule(Rule):
    severity: Severity = "deny"


class DelegatecallRule(Rule):
    severity: Severity = "deny"
    allow_targets: list[Entry] = Field(default_factory=list)


class BlindSigningRule(Rule):
    severity: Severity = "deny"


class UpgradeRule(Rule):
    """`severity` applies outside every change window. Inside one, the rule holds for quorum."""

    severity: Severity = "deny"
    change_windows: list[ChangeWindow] = Field(default_factory=list)


class UnlimitedApprovalRule(Rule):
    severity: Severity = "deny"
    threshold: int = Field(default=2**96 - 1, ge=1)


class ApprovalAllowlistRule(Rule):
    severity: Severity = "deny"
    spenders: list[Entry] = Field(default_factory=list)


class IntentRule(Rule):
    severity: Severity = "deny"


class VelocityRule(Rule):
    severity: Severity = "hold"
    window: Duration
    limit_usd: Usd


class MaxPerTxRule(Rule):
    severity: Severity = "hold"
    limit_usd: Usd


class DestinationRule(Rule):
    severity: Severity = "hold"
    roles: list[str] = Field(default_factory=lambda: ["treasury"])
    destinations: list[Entry] = Field(default_factory=list)


class ExplainableRule(Rule):
    severity: Severity = "hold"
    contracts: list[Contract] = Field(default_factory=list)


class Rules(_Model):
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    no_eip7702_delegation: DelegationRule = Field(alias="no-eip7702-delegation")
    no_delegatecall: DelegatecallRule = Field(alias="no-delegatecall")
    no_blind_signing: BlindSigningRule = Field(alias="no-blind-signing")
    no_upgrade_or_owner_change: UpgradeRule = Field(alias="no-upgrade-or-owner-change")
    no_unlimited_approval: UnlimitedApprovalRule = Field(alias="no-unlimited-approval")
    approval_allowlist: ApprovalAllowlistRule = Field(alias="approval-allowlist")
    intent_required: IntentRule = Field(alias="intent-required")
    velocity: VelocityRule
    max_per_tx: MaxPerTxRule = Field(alias="max-per-tx")
    destination_allowlist: DestinationRule = Field(alias="destination-allowlist")
    explainable: ExplainableRule

    def get(self, rule_id: str) -> Rule:
        """The configuration of the rule with this stable id, such as `no-delegatecall`."""
        rule = getattr(self, rule_id.replace("-", "_"))
        assert isinstance(rule, Rule)
        return rule


class Approver(_Model):
    id: str = Field(min_length=1)
    public_key: PublicKey


class Quorum(_Model):
    """M of the listed approvers can release a hold. No approvers means no hold is ever released."""

    threshold: int = Field(ge=1)
    approvers: list[Approver] = Field(default_factory=list)

    @model_validator(mode="after")
    def _consistent(self) -> Quorum:
        ids = [a.id for a in self.approvers]
        if len(set(ids)) != len(ids):
            raise ValueError("approver ids must be unique")
        if len({a.public_key for a in self.approvers}) != len(ids):
            raise ValueError("each approver needs their own key, or one person could count twice")
        if self.approvers and self.threshold > len(self.approvers):
            raise ValueError(f"threshold {self.threshold} is more than the {len(ids)} approvers listed")
        return self


class Policy(_Model):
    version: Literal[1]
    name: str = Field(min_length=1)
    wallets: list[Wallet] = Field(default_factory=list)
    assets: list[Asset] = Field(default_factory=list)
    rules: Rules
    quorum: Quorum

    _wallets: dict[str, Wallet] = PrivateAttr(default_factory=dict)
    _assets: dict[tuple[int, str], Asset] = PrivateAttr(default_factory=dict)

    @model_validator(mode="after")
    def _index(self) -> Policy:
        self._wallets = {w.address: w for w in self.wallets}
        if len(self._wallets) != len(self.wallets):
            raise ValueError("a wallet is listed twice")
        self._assets = {(a.chain_id, a.address): a for a in self.assets}
        if len(self._assets) != len(self.assets):
            raise ValueError("an asset is listed twice for the same chain")
        return self

    def wallet(self, address: str) -> Wallet | None:
        return self._wallets.get(address)

    def asset(self, chain_id: int, asset: str) -> Asset | None:
        return self._assets.get((chain_id, asset))

    def label(self, address: str) -> str:
        """A human name for an address the policy knows: a wallet, a listed asset or an allowlist entry."""
        wallet = self._wallets.get(address)
        if wallet:
            return wallet.label
        rules = self.rules
        lists: list[list[Entry]] = [
            rules.no_delegatecall.allow_targets,
            rules.approval_allowlist.spenders,
            rules.destination_allowlist.destinations,
            list(rules.explainable.contracts),
        ]
        for entries in lists:
            for entry in entries:
                if entry.address == address and entry.label:
                    return entry.label
        for asset in self.assets:
            if asset.address == address:
                return asset.symbol
        return ""

    @property
    def digest(self) -> str:
        """SHA-256 of the policy's canonical JSON. Comments and key order do not change it."""
        canonical = json.dumps(self.model_dump(mode="json", by_alias=True), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode()).hexdigest()

    def change_window(self, now: datetime) -> ChangeWindow | None:
        for window in self.rules.no_upgrade_or_owner_change.change_windows:
            if window.start <= now < window.end:
                return window
        return None


class _UniqueKeyLoader(yaml.SafeLoader):
    """A safe YAML loader that refuses a key repeated in one mapping.

    PyYAML keeps the last of two equal keys without a word, so a second `quorum:` or `severity:`
    further down a file would silently replace the one a reviewer read.
    """


def _unique_mapping(loader: _UniqueKeyLoader, node: yaml.MappingNode) -> dict[object, object]:
    loader.flatten_mapping(node)
    seen: set[object] = set()
    for key_node, _ in node.value:
        key = loader.construct_object(key_node, deep=True)
        if isinstance(key, str | int | float | bool):
            if key in seen:
                raise yaml.constructor.ConstructorError(
                    "while reading a mapping", node.start_mark, f"the key {key!r} appears twice", key_node.start_mark
                )
            seen.add(key)
    return loader.construct_mapping(node, deep=True)


_UniqueKeyLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _unique_mapping)


def load_policy(source: Path | str) -> Policy:
    """Load a policy from a YAML file path, or from YAML text. Raises ValueError for any problem."""
    text = source.read_text(encoding="utf-8") if isinstance(source, Path) else source
    try:
        data = yaml.load(text, Loader=_UniqueKeyLoader)
    except yaml.YAMLError as exc:
        raise ValueError(f"the policy is not valid YAML: {exc}") from exc
    return Policy.model_validate(data)
