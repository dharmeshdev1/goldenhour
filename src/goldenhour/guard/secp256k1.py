"""secp256k1 signer recovery, the one piece of elliptic curve arithmetic Guard needs.

Guard has to know who signed an EIP-7702 authorization, and who signed a raw transaction it is
shown. The `cryptography` package verifies secp256k1 signatures but cannot recover a signer from
one, so recovery is done here. It works on public data only: Guard approves secp256k1 signatures,
it never makes them, so nothing here touches a private key except the derivation of the published
demonstration addresses.
"""

from __future__ import annotations

from goldenhour.guard.evm import keccak256

P = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEFFFFFC2F
N = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141
HALF_N = N // 2
G = (
    0x79BE667EF9DCBBAC55A06295CE870B07029BFCDB2DCE28D959F2815B16F81798,
    0x483ADA7726A3C4655DA4FBFC0E1108A8FD17B448A68554199C47D08FFB10D4B8,
)

type Point = tuple[int, int] | None


def _add(a: Point, b: Point) -> Point:
    if a is None:
        return b
    if b is None:
        return a
    (x1, y1), (x2, y2) = a, b
    if x1 == x2:
        if (y1 + y2) % P == 0:
            return None
        slope = 3 * x1 * x1 * pow(2 * y1, -1, P) % P
    else:
        slope = (y2 - y1) * pow(x2 - x1, -1, P) % P
    x3 = (slope * slope - x1 - x2) % P
    return x3, (slope * (x1 - x3) - y1) % P


def _multiply(k: int, point: Point) -> Point:
    result: Point = None
    while k:
        if k & 1:
            result = _add(result, point)
        point = _add(point, point)
        k >>= 1
    return result


def public_key_to_address(point: tuple[int, int]) -> str:
    x, y = point
    return "0x" + keccak256(x.to_bytes(32, "big") + y.to_bytes(32, "big"))[12:].hex()


def private_key_to_address(private_key: int) -> str:
    point = _multiply(private_key, G)
    assert point is not None
    return public_key_to_address(point)


def recover_address(msg_hash: bytes, y_parity: int, r: int, s: int) -> str:
    """The address whose key produced signature (y_parity, r, s) over `msg_hash`.

    Raises ValueError for a signature no key could have produced. High `s` values are accepted
    here; the callers that need EIP-2's low-`s` rule apply it themselves.
    """
    if len(msg_hash) != 32:
        raise ValueError("the signed hash must be 32 bytes")
    if y_parity not in (0, 1) or not 0 < r < N or not 0 < s < N:
        raise ValueError("signature values are out of range")
    y_squared = (pow(r, 3, P) + 7) % P
    y = pow(y_squared, (P + 1) // 4, P)
    if y * y % P != y_squared:
        raise ValueError("r is not the x coordinate of a point on the curve")
    if y & 1 != y_parity:
        y = P - y
    r_inverse = pow(r, -1, N)
    e = int.from_bytes(msg_hash, "big")
    point = _add(_multiply(s * r_inverse % N, (r, y)), _multiply(-e * r_inverse % N, G))
    if point is None:
        raise ValueError("signature recovers to the point at infinity")
    return public_key_to_address(point)
