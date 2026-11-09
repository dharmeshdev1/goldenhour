"""Ed25519 keys: one for the guard itself, one for each person who may approve a held request.

Approvals and audit records are both short statements that anyone holding a 32-byte public key must
be able to check years later. Ed25519 suits both: verification needs no parameters beyond the key,
and signatures are deterministic, so the same statement always produces the same bytes.
"""

from __future__ import annotations

import os
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from goldenhour.guard.evm import parse_hex


def generate() -> Ed25519PrivateKey:
    return Ed25519PrivateKey.generate()


def from_seed(seed: bytes) -> Ed25519PrivateKey:
    """A key from a 32-byte seed. The demonstration cases use published seeds; real keys never should."""
    return Ed25519PrivateKey.from_private_bytes(seed)


def public_key_hex(key: Ed25519PrivateKey | Ed25519PublicKey) -> str:
    public = key.public_key() if isinstance(key, Ed25519PrivateKey) else key
    return public.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw).hex()


def parse_public_key(value: str) -> Ed25519PublicKey:
    """A public key written as 64 hex digits, with or without a 0x prefix."""
    text = value.strip()
    raw = parse_hex(text if text.startswith("0x") else "0x" + text)
    if len(raw) != 32:
        raise ValueError(f"an Ed25519 public key is 32 bytes, got {len(raw)}")
    return Ed25519PublicKey.from_public_bytes(raw)


def save_private_key(key: Ed25519PrivateKey, path: Path) -> None:
    """Write a PKCS#8 PEM file readable only by its owner. Refuses to replace an existing key."""
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(pem)


def load_private_key(path: Path) -> Ed25519PrivateKey:
    key = serialization.load_pem_private_key(path.read_bytes(), password=None)
    if not isinstance(key, Ed25519PrivateKey):
        raise ValueError(f"{path} does not hold an Ed25519 private key")
    return key


def verify(public_key: Ed25519PublicKey, signature: bytes, message: bytes) -> bool:
    try:
        public_key.verify(signature, message)
    except InvalidSignature:
        return False
    return True
