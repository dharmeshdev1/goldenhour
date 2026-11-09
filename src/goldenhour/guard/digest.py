"""The two hashes Guard puts in front of humans: the request digest and the signing hash.

The signing hash is what the wallet's key actually signs: keccak256 of the transaction payload, of
the EIP-712 encoding, or of the message with its prefix. Guard computes it from the request it has
read, and a signer behind Guard must sign that hash and no hash a caller supplies, otherwise Guard
would be vouching for one thing while the key signed another.

The request digest is what approvers sign. It binds the signing preimage to the context the policy
judged it in (the kind of request, the chain, the wallet and the withdrawal intent) under a domain
tag, so an approval for one request can never be replayed for another. Approvals and the caller's
prices are left out: they are inputs to the decision, not part of what is approved.
"""

from __future__ import annotations

from goldenhour.guard import eip712, rlp
from goldenhour.guard.evm import address_to_bytes, keccak256
from goldenhour.guard.request import SigningRequest
from goldenhour.guard.transaction import parse_authorization_message, unsigned_payload

REQUEST_DOMAIN = b"goldenhour/guard/request/v1"
APPROVAL_DOMAIN = b"goldenhour/guard/approval/v1"


def signing_preimage(request: SigningRequest) -> bytes:
    """The bytes whose keccak256 the wallet's key would sign."""
    if request.kind == "evm_tx":
        return unsigned_payload(request)
    if request.kind == "evm_typed_data":
        assert request.typed_data is not None
        return eip712.signing_preimage(request.typed_data)
    assert request.message is not None
    if parse_authorization_message(request.message) is not None:
        return request.message  # EIP-7702 authorizations are signed without a prefix
    return b"\x19Ethereum Signed Message:\n" + str(len(request.message)).encode() + request.message


def signing_hash(request: SigningRequest) -> bytes:
    return keccak256(signing_preimage(request))


def request_digest(request: SigningRequest) -> bytes:
    """keccak256 of rlp([domain tag, kind, chain_id, wallet, intent_id, signing preimage])."""
    return keccak256(
        rlp.encode(
            [
                REQUEST_DOMAIN,
                request.kind.encode(),
                rlp.encode_int(request.chain_id),
                address_to_bytes(request.wallet),
                (request.intent_id or "").encode(),
                signing_preimage(request),
            ]
        )
    )


def approval_message(digest: bytes) -> bytes:
    """What an approver's Ed25519 key signs: the digest under its own domain tag."""
    return APPROVAL_DOMAIN + digest
