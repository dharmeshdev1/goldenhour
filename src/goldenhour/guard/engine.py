"""The decision: run the rules, weigh their findings, and apply the quorum.

`evaluate` is a pure function of (request, policy, ledger state, now), with the caller's prices
inside the request. It reads the ledger and changes nothing, so the same inputs always give the
same decision, byte for byte, and any decision in the audit log can be replayed and checked.

Precedence is fixed: any deny wins; otherwise any hold wins unless a quorum of approvers has signed
this exact request; otherwise the request is allowed. A quorum can release a hold but never a deny.

`Guard` wraps `evaluate` with the two side effects a real decision has: consuming the intent and
recording the spending when a request is allowed, and appending every decision to the audit log.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, Literal

from goldenhour.guard.audit import AuditLog
from goldenhour.guard.decode import Action, DecodeError, decode
from goldenhour.guard.digest import approval_message, request_digest, signing_hash
from goldenhour.guard.evm import parse_hex, to_checksum_address
from goldenhour.guard.keys import parse_public_key, verify
from goldenhour.guard.ledger import Ledger, LedgerView, Spend
from goldenhour.guard.policy import RULE_IDS, Policy
from goldenhour.guard.request import Approval, SigningRequest
from goldenhour.guard.rules import RULES, Context, Finding

type Outcome = Literal["allow", "hold", "deny"]

_EFFECT_ORDER = {"deny": 0, "hold": 1, "note": 2}


@dataclass(frozen=True)
class QuorumResult:
    """Which approvals counted, and why the others did not."""

    required: int
    approvers: tuple[str, ...]
    rejected: tuple[tuple[str, str], ...]

    @property
    def met(self) -> bool:
        return len(self.approvers) >= self.required


@dataclass(frozen=True)
class Decision:
    outcome: Outcome
    findings: tuple[Finding, ...]
    actions: tuple[Action, ...]
    request_digest: str
    signing_hash: str
    policy_digest: str
    evaluated_at: datetime
    quorum: QuorumResult
    intent: str | None  # the intent an allow consumes
    spends: tuple[Spend, ...]  # USD leaving each wallet, recorded on allow

    def reasons(self) -> list[dict[str, str]]:
        return [{"rule": f.rule, "message": f.message} for f in self.findings]

    def to_json(self) -> dict[str, Any]:
        return {
            "decision": self.outcome,
            "reasons": [
                {"rule": f.rule, "effect": f.effect, "message": f.message, "path": f.path} for f in self.findings
            ],
            "request_digest": self.request_digest,
            "signing_hash": self.signing_hash,
            "policy_digest": self.policy_digest,
            "evaluated_at": self.evaluated_at.isoformat().replace("+00:00", "Z"),
            "quorum": {
                "required": self.quorum.required,
                "approvers": list(self.quorum.approvers),
                "rejected": [{"approver": a, "reason": r} for a, r in self.quorum.rejected],
            },
            "intent": self.intent,
            "spends": [
                {
                    "wallet": to_checksum_address(s.wallet),
                    "usd": f"{s.usd.normalize():f}",
                    "limit_usd": None if s.limit is None else f"{s.limit.normalize():f}",
                }
                for s in self.spends
            ],
            "actions": [a.to_json() for a in self.actions],
        }


def check_quorum(policy: Policy, digest: bytes, approvals: list[Approval]) -> QuorumResult:
    """Count valid Ed25519 approvals of `digest` from distinct approvers the policy lists."""
    keys = {a.id: parse_public_key(a.public_key) for a in policy.quorum.approvers}
    message = approval_message(digest)
    counted: list[str] = []
    rejected: list[tuple[str, str]] = []
    for approval in approvals:
        key = keys.get(approval.approver)
        if key is None:
            rejected.append((approval.approver, "not an approver in this policy"))
            continue
        text = approval.signature.strip()
        try:
            signature = parse_hex(text if text.startswith("0x") else "0x" + text)
        except ValueError:
            signature = b""
        if len(signature) != 64 or not verify(key, signature, message):
            rejected.append((approval.approver, "signature does not verify against this request's digest"))
        elif approval.approver in counted:
            rejected.append((approval.approver, "approved more than once, counted once"))
        else:
            counted.append(approval.approver)
    return QuorumResult(policy.quorum.threshold, tuple(counted), tuple(rejected))


def evaluate(request: SigningRequest, policy: Policy, ledger: LedgerView, now: datetime) -> Decision:
    """Decide a request. Pure: reads `ledger`, never writes it."""
    if now.tzinfo is None:
        raise ValueError("now must carry a timezone")
    digest = request_digest(request)
    quorum = check_quorum(policy, digest, request.approvals)
    findings: list[Finding] = []
    if policy.wallet(request.wallet) is None:
        findings.append(
            Finding(
                "known-wallet",
                "deny",
                f"{to_checksum_address(request.wallet)} is not a wallet this policy protects, so Guard will not "
                "sign for it.",
            )
        )
    actions: tuple[Action, ...] = ()
    ctx = None
    try:
        actions = tuple(decode(request))
    except Exception as exc:  # a request Guard cannot read in full is refused, whatever the reason
        reason = str(exc) if isinstance(exc, DecodeError) else f"{type(exc).__name__}: {exc}"
        path = exc.path if isinstance(exc, DecodeError) else None
        findings.append(
            Finding("well-formed", "deny", f"Guard cannot read this request ({reason}), so it refuses it.", path)
        )
    else:
        ctx = Context(request, policy, actions, ledger, now)
        for rule_id, check in RULES:
            if policy.rules.get(rule_id).enabled:
                findings.extend(check(ctx))

    effects = {f.effect for f in findings}
    if "deny" in effects:
        outcome: Outcome = "deny"
        if quorum.approvers:
            findings.append(
                Finding("quorum", "note", "Approvals were ignored: a quorum can release a hold, not a deny.")
            )
    elif "hold" in effects:
        outcome = "allow" if quorum.met else "hold"
        names = ", ".join(quorum.approvers)
        if quorum.met:
            message = f"Released by {len(quorum.approvers)} valid approvals ({names}), with {quorum.required} required."
        else:
            have = f" ({names})" if names else ""
            message = (
                f"Held for {quorum.required} approvals of request digest 0x{digest.hex()}. "
                f"Valid so far: {len(quorum.approvers)}{have}."
            )
        findings.append(Finding("quorum", "note", message))
    else:
        outcome = "allow"

    order = {rule: index for index, rule in enumerate(RULE_IDS)}
    findings = sorted(findings, key=lambda f: (_EFFECT_ORDER[f.effect], order[f.rule]))
    spends: tuple[Spend, ...] = ()
    intent = None
    if outcome == "allow" and ctx is not None:
        intent = ctx.matched_intent if policy.rules.intent_required.enabled else None
        spends = _spends(ctx, findings)
    return Decision(
        outcome=outcome,
        findings=tuple(findings),
        actions=actions,
        request_digest="0x" + digest.hex(),
        signing_hash="0x" + signing_hash(request).hex(),
        policy_digest=policy.digest,
        evaluated_at=now,
        quorum=quorum,
        intent=intent,
        spends=spends,
    )


def _spends(ctx: Context, findings: list[Finding]) -> tuple[Spend, ...]:
    """What an allow records per wallet, with the velocity limit the commit must still respect.

    The limit travels with the spend so the ledger can re-check it under its write lock: two
    requests evaluated at the same moment would otherwise both see room under the limit. A request
    whose velocity hold was released by a quorum carries no limit, because approvers chose to exceed it.
    """
    totals: dict[str, Decimal] = {}
    for transfer, value in zip(ctx.transfers, ctx.values, strict=True):
        if value is not None:
            totals[transfer.source] = totals.get(transfer.source, Decimal(0)) + value
    rule = ctx.policy.rules.velocity
    enforce = rule.enabled and not any(f.rule == "velocity" and f.effect != "note" for f in findings)
    spends = []
    for wallet, usd in totals.items():
        if not enforce:
            spends.append(Spend(wallet, usd))
            continue
        entry = ctx.policy.wallet(wallet)
        limit = entry.velocity_limit_usd if entry and entry.velocity_limit_usd is not None else rule.limit_usd
        spends.append(Spend(wallet, usd, limit, ctx.now - timedelta(seconds=rule.window)))
    return tuple(spends)


class Guard:
    """A policy, a ledger and an audit log: the stateful service around `evaluate`."""

    def __init__(self, policy: Policy, ledger: Ledger, audit: AuditLog | None = None) -> None:
        self.policy = policy
        self.ledger = ledger
        self.audit = audit

    def check(self, request: SigningRequest, now: datetime) -> Decision:
        """Decide, commit an allow to the ledger, and log the decision.

        If the ledger refuses the commit because another request consumed the intent first, or spent
        the wallet's remaining velocity allowance first, the request is decided again against the new
        state, which then denies or holds it.
        """
        for _ in range(3):
            decision = evaluate(request, self.policy, self.ledger, now)
            if decision.outcome != "allow" or self.ledger.commit(
                intent_id=decision.intent, digest=decision.request_digest, spends=decision.spends, at=now
            ):
                break
        else:
            raise RuntimeError("the ledger kept refusing to record an allowed request")
        if self.audit is not None:
            self.audit.append({"type": "decision", "request": request.to_json(), "decision": decision.to_json()}, now)
        return decision
