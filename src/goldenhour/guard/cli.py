"""`goldenhour guard`: check signing requests, approve held ones, manage keys, intents and the audit log.

Exit status of `check` is part of its interface, so scripts can gate a signer on it without
parsing output: 0 allow, 3 hold, 4 deny, 2 for a usage or configuration error.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any

import typer
from rich.console import Console
from rich.markup import escape
from rich.table import Table

guard_app = typer.Typer(
    name="guard",
    no_args_is_help=True,
    add_completion=False,
    help="Policy co-signer: decide signing requests, approve held ones, verify the audit log.",
)
# Hashes and addresses must never be split across lines. Text that came from a request is always
# escaped, so a crafted message cannot use console markup to hide part of a reason from a reviewer.
console = Console(soft_wrap=True)

EXIT_CODES = {"allow": 0, "hold": 3, "deny": 4}
EXISTING_FILE = {"exists": True, "dir_okay": False, "readable": True}


def _fail(message: str) -> typer.Exit:
    console.print(f"[red]error:[/] {escape(message)}", highlight=False)
    return typer.Exit(2)


def _moment(value: str | None) -> datetime:
    if value is None:
        return datetime.now(UTC)
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise _fail(f"--now is not an ISO 8601 time: {value}") from exc
    if moment.tzinfo is None:
        raise _fail("--now needs a timezone, such as 2026-11-01T12:00:00Z")
    return moment


def _print_decision(body: dict[str, Any]) -> None:
    """The decision JSON as a table. Request text is escaped, so markup in it prints as written."""
    outcome = body["decision"]
    colour = {"allow": "green", "hold": "yellow", "deny": "red"}[outcome]
    console.print(f"[bold {colour}]{outcome.upper()}[/]")
    table = Table(show_header=True, header_style="bold", expand=True)
    table.add_column("effect", no_wrap=True)
    table.add_column("rule", no_wrap=True)
    table.add_column("reason")
    for reason in body["reasons"]:
        table.add_row(reason["effect"], reason["rule"], escape(reason["message"]))
    if body["reasons"]:
        console.print(table)
    labels = (
        ("actions", ", ".join(a["kind"] for a in body.get("actions", [])) or "none"),
        ("request digest", body.get("request_digest")),
        ("signing hash", body.get("signing_hash")),
        ("policy digest", body.get("policy_digest")),
        ("audit record", body.get("audit_record")),
    )
    for label, value in labels:
        if value:
            console.print(f"{label + ':':<16}{value}", highlight=False)


@guard_app.command()
def check(
    request: Annotated[Path, typer.Argument(help="Signing request JSON.", **EXISTING_FILE)],
    policy: Annotated[Path, typer.Option("--policy", "-p", help="Policy YAML.", **EXISTING_FILE)],
    ledger: Annotated[
        Path | None, typer.Option(help="SQLite ledger of withdrawal intents. Without one, no transfer has an intent.")
    ] = None,
    log: Annotated[Path | None, typer.Option(help="Append the decision to this audit log. Needs --key.")] = None,
    key: Annotated[Path | None, typer.Option(help="The guard's Ed25519 private key, which signs the log.")] = None,
    now: Annotated[str | None, typer.Option(help="Decide as of this ISO 8601 time, not the clock.")] = None,
    as_json: Annotated[bool, typer.Option("--json", help="Print the decision as JSON.")] = False,
) -> None:
    """Decide one signing request: allow, hold or deny, with the reasons."""
    from goldenhour.guard.audit import AuditLog
    from goldenhour.guard.engine import Guard
    from goldenhour.guard.keys import load_private_key
    from goldenhour.guard.ledger import Ledger, MemoryLedger, SqliteLedger
    from goldenhour.guard.policy import load_policy
    from goldenhour.guard.service import decide

    if log is not None and key is None:
        raise _fail("--log needs --key, the guard's private key")
    moment = _moment(now)
    try:
        rules = load_policy(policy)
    except ValueError as exc:
        raise _fail(f"{policy} is not a valid policy:\n{exc}") from exc
    try:
        audit = AuditLog(log, load_private_key(key)) if log is not None and key is not None else None
    except ValueError as exc:
        raise _fail(str(exc)) from exc
    sqlite = SqliteLedger(ledger) if ledger is not None else None
    store: Ledger = sqlite if sqlite is not None else MemoryLedger()
    try:
        body = decide(request.read_text(encoding="utf-8"), Guard(rules, store, audit), moment)
    finally:
        if sqlite is not None:
            sqlite.close()
    if as_json:
        print(json.dumps(body, indent=2))
    else:
        _print_decision(body)
    raise typer.Exit(EXIT_CODES[body["decision"]])


@guard_app.command()
def keygen(
    out: Annotated[Path, typer.Argument(help="Where to write the new private key (PEM). Must not exist yet.")],
) -> None:
    """Create an Ed25519 key for the guard or for an approver, and print its public key."""
    from goldenhour.guard.keys import generate, public_key_hex, save_private_key

    key = generate()
    try:
        save_private_key(key, out)
    except FileExistsError as exc:
        raise _fail(f"{out} already exists; Guard never overwrites a key") from exc
    public = public_key_hex(key)
    out.with_name(out.name + ".pub").write_text(public + "\n", encoding="utf-8")
    console.print(public, highlight=False)


@guard_app.command()
def approve(
    request: Annotated[Path, typer.Argument(help="The held signing request JSON.", **EXISTING_FILE)],
    key: Annotated[Path, typer.Option(help="Your Ed25519 private key.", **EXISTING_FILE)],
    approver: Annotated[str, typer.Option(help="Your approver id in the policy.")],
    write: Annotated[bool, typer.Option("--write", help="Add the approval to the request file.")] = False,
) -> None:
    """Sign a request's digest. Read the reasons `check` gives for the same request first."""
    from pydantic import ValidationError

    from goldenhour.guard.digest import approval_message, request_digest
    from goldenhour.guard.keys import load_private_key
    from goldenhour.guard.request import SigningRequest

    text = request.read_text(encoding="utf-8")
    try:
        digest = request_digest(SigningRequest.model_validate_json(text))
    except ValidationError as exc:
        raise _fail(f"{request} is not a valid signing request:\n{exc}") from exc
    approval = {"approver": approver, "signature": load_private_key(key).sign(approval_message(digest)).hex()}
    if write:
        data = json.loads(text)
        data["approvals"] = [*data.get("approvals", []), approval]
        request.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"request_digest": "0x" + digest.hex(), **approval}))


@guard_app.command("verify-log")
def verify_log(
    log: Annotated[Path, typer.Argument(help="The audit log (JSONL).", **EXISTING_FILE)],
    public_key: Annotated[str, typer.Option(help="The guard's public key: 64 hex digits, or a file holding them.")],
    head: Annotated[
        str | None, typer.Option(help="A head hash published earlier. It must still be in the log.")
    ] = None,
) -> None:
    """Check the hash chain and every signature, and report the first record that fails."""
    from goldenhour.guard.audit import verify
    from goldenhour.guard.keys import parse_public_key

    source = Path(public_key)
    text = source.read_text(encoding="utf-8") if source.is_file() else public_key
    try:
        key = parse_public_key(text)
    except ValueError as exc:
        raise _fail(f"not an Ed25519 public key: {exc}") from exc
    report = verify(log, key, head)
    if not report.ok:
        where = f" (line {report.line})" if report.line else ""
        console.print(f"[bold red]BROKEN[/]{where}: {escape(report.reason)}", highlight=False)
        console.print(f"{report.records} records verified before it, ending at {report.head}")
        raise typer.Exit(1)
    console.print(f"[bold green]OK[/] {report.records} records, head {report.head}", highlight=False)
    if report.after_expected is not None:
        console.print(f"the published head is in the log, with {report.after_expected} records after it")


@guard_app.command("add-intent")
def add_intent(
    intents: Annotated[Path, typer.Argument(help="One intent as a JSON object, or a list of them.", **EXISTING_FILE)],
    ledger: Annotated[Path, typer.Option(help="SQLite ledger file. Created if missing.")],
) -> None:
    """Record withdrawal intents, as the withdrawal system does when a payout is approved."""
    from pydantic import ValidationError

    from goldenhour.guard.ledger import Intent, SqliteLedger

    data = json.loads(intents.read_text(encoding="utf-8"))
    try:
        items = [Intent.model_validate(item) for item in (data if isinstance(data, list) else [data])]
    except ValidationError as exc:
        raise _fail(f"{intents} holds an invalid intent:\n{exc}") from exc
    store = SqliteLedger(ledger)
    try:
        for item in items:
            store.add_intent(item)
    except ValueError as exc:
        raise _fail(str(exc)) from exc
    finally:
        store.close()
    console.print(f"added {len(items)} intent{'s' if len(items) != 1 else ''} to {ledger}", highlight=False)
