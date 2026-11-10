"""Guard as a service: what the self-hosted server runs for `POST /v1/guard/evaluate`.

`evaluate_json` takes a signing request as parsed JSON and returns the decision as JSON, exactly as
`goldenhour guard check --json` prints it. It never raises for a bad request: a request Guard cannot
read is denied and logged like any other decision.

It is configured from the environment, so one image runs anywhere:

* `GOLDENHOUR_GUARD_POLICY`: the policy file. Default: the shipped `policies/default.yaml`, which
  denies everything until wallets are listed in it.
* `GOLDENHOUR_STATE`: the state directory, default `state`. It holds the ledger
  (`guard-ledger.db`) and the audit log (`guard-audit.jsonl`).
* `GOLDENHOUR_GUARD_KEY`: the guard's Ed25519 seed as 64 hex digits. Without it, a key is created
  on first use at `guard-key.pem` in the state directory, readable only by its owner, with its
  public key beside it in `guard-key.pem.pub` for `verify-log`.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from pydantic import ValidationError

from goldenhour.guard import keys
from goldenhour.guard.audit import AuditLog
from goldenhour.guard.engine import Guard
from goldenhour.guard.evm import parse_hex
from goldenhour.guard.ledger import SqliteLedger
from goldenhour.guard.policy import DEFAULT_POLICY, load_policy
from goldenhour.guard.request import SigningRequest


def decide(payload: Mapping[str, Any] | str, guard: Guard, now: datetime) -> dict[str, Any]:
    """Validate a request (JSON text or parsed JSON), decide it, log it, and return the decision JSON.

    Anything that stops the request from being read, whatever raised it, becomes a logged deny.
    """
    try:
        if isinstance(payload, str):
            request = SigningRequest.model_validate_json(payload)
        else:
            request = SigningRequest.model_validate(payload)
    except Exception as exc:  # a request Guard cannot read is refused, never crashed on
        prefix = "Guard cannot read this request, so it refuses it: "
        if isinstance(exc, ValidationError):
            # Pydantic's messages describe the request, not the server: the caller needs them.
            first = exc.errors()[0]
            shown = f"{'.'.join(str(part) for part in first['loc']) or 'request'}: {first['msg']}"
            detail = shown
        else:
            # An unexpected failure is described in the audit log only. Its text can name files or
            # internals, so the caller gets a fixed reason.
            shown = "it is not a signing request Guard can decode"
            detail = f"{type(exc).__name__}: {exc}"
        head = None
        if guard.audit is not None:
            text = payload if isinstance(payload, str) else json.dumps(payload, sort_keys=True, default=str)
            digest = hashlib.sha256(text.encode()).hexdigest()
            guard.audit.append({"type": "unreadable", "request_sha256": digest, "reason": prefix + detail}, now)
            head = guard.audit.head
        finding = {"rule": "well-formed", "effect": "deny", "message": prefix + shown, "path": None}
        return {"decision": "deny", "reasons": [finding], "audit_record": head}
    decision = guard.check(request, now)
    return {**decision.to_json(), "audit_record": guard.audit.head if guard.audit is not None else None}


def guard_key(state: Path) -> Ed25519PrivateKey:
    """The guard's key: from GOLDENHOUR_GUARD_KEY, or a key file in the state directory."""
    seed = os.environ.get("GOLDENHOUR_GUARD_KEY", "").strip()
    if seed:
        raw = parse_hex(seed if seed.startswith("0x") else "0x" + seed)
        if len(raw) != 32:
            raise ValueError("GOLDENHOUR_GUARD_KEY must be a 32-byte Ed25519 seed, as 64 hex digits")
        return keys.from_seed(raw)
    path = state / "guard-key.pem"
    if not path.exists():
        key = keys.generate()
        try:
            keys.save_private_key(key, path)
        except FileExistsError:
            pass  # another process created it first; use theirs
        else:
            path.with_name(path.name + ".pub").write_text(keys.public_key_hex(key) + "\n", encoding="utf-8")
    return keys.load_private_key(path)


def evaluate_json(body: dict[str, Any]) -> dict[str, Any]:
    """Decide one signing request with the configured policy, ledger and audit log."""
    state = Path(os.environ.get("GOLDENHOUR_STATE", "state"))
    state.mkdir(parents=True, exist_ok=True)
    policy = load_policy(Path(os.environ.get("GOLDENHOUR_GUARD_POLICY") or DEFAULT_POLICY))
    audit = AuditLog(state / "guard-audit.jsonl", guard_key(state))
    ledger = SqliteLedger(state / "guard-ledger.db")
    try:
        return decide(body, Guard(policy, ledger, audit), datetime.now(UTC))
    finally:
        ledger.close()
