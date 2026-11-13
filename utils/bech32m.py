"""Decode Chia ``nft1`` ids.

Chia's NFT id is a bech32m string whose payload is the 32-byte launcher id.
``create_offer_for_ids`` asks for that launcher id as hex, so the sale offer
has to decode the id the wallet returns from ``nft_mint_nft``.
"""

from __future__ import annotations

CHARSET = "qpzry9x8gf2tvdw0s3jn54khce6mua7l"
BECH32M_CONST = 0x2BC830A3
_GENERATORS = (0x3B6A57B2, 0x26508E6D, 0x1EA119FA, 0x3D4233DD, 0x2A1462B3)


class Bech32Error(ValueError):
    """The string is not a valid bech32m NFT id."""


def nft_id_to_launcher_hex(nft_id: str) -> str:
    """Return the launcher id hex encoded by an ``nft1`` id."""
    decoded = decode_nft_launcher(nft_id.strip())
    return decoded.hex()


def decode_nft_launcher(nft_id: str) -> bytes:
    """Return the 32 launcher bytes stored in an ``nft1`` id."""
    hrp, data = _bech32_decode(nft_id)
    if hrp != "nft" or data is None:
        raise Bech32Error("NFT id must be an nft1 id.")
    converted = _convertbits(data, 5, 8, pad=False)
    if converted is None or len(converted) != 32:
        raise Bech32Error("NFT id did not contain a 32-byte launcher id.")
    return bytes(converted)


def encode_nft_id(launcher: bytes) -> str:
    """Encode 32 launcher bytes as an ``nft1`` id. Used to test the decoder."""
    if len(launcher) != 32:
        raise Bech32Error("A launcher id is 32 bytes.")
    converted = _convertbits(list(launcher), 8, 5, pad=True)
    if converted is None:
        raise Bech32Error("Could not encode the launcher id.")
    return _bech32_encode("nft", converted)


def _bech32_polymod(values: list[int]) -> int:
    checksum = 1
    for value in values:
        top = checksum >> 25
        checksum = ((checksum & 0x1FFFFFF) << 5) ^ value
        for index, generator in enumerate(_GENERATORS):
            if (top >> index) & 1:
                checksum ^= generator
    return checksum


def _hrp_expand(hrp: str) -> list[int]:
    return [ord(char) >> 5 for char in hrp] + [0] + [ord(char) & 31 for char in hrp]


def _create_checksum(hrp: str, data: list[int]) -> list[int]:
    polymod = _bech32_polymod(_hrp_expand(hrp) + data + [0, 0, 0, 0, 0, 0]) ^ BECH32M_CONST
    return [(polymod >> 5 * (5 - index)) & 31 for index in range(6)]


def _bech32_encode(hrp: str, data: list[int]) -> str:
    combined = data + _create_checksum(hrp, data)
    return hrp + "1" + "".join(CHARSET[value] for value in combined)


def _bech32_decode(value: str) -> tuple[str | None, list[int] | None]:
    if not value or any(ord(char) < 33 or ord(char) > 126 for char in value):
        return None, None
    if value.lower() != value and value.upper() != value:
        return None, None
    value = value.lower()
    separator = value.rfind("1")
    if separator < 1 or separator + 7 > len(value):
        return None, None
    hrp = value[:separator]
    data: list[int] = []
    for char in value[separator + 1 :]:
        index = CHARSET.find(char)
        if index < 0:
            return None, None
        data.append(index)
    if _bech32_polymod(_hrp_expand(hrp) + data) != BECH32M_CONST:
        return None, None
    return hrp, data[:-6]


def _convertbits(data: list[int], from_bits: int, to_bits: int, *, pad: bool) -> list[int] | None:
    accumulator = 0
    bits = 0
    result: list[int] = []
    limit = (1 << to_bits) - 1
    for value in data:
        if value < 0 or value >> from_bits:
            return None
        accumulator = (accumulator << from_bits) | value
        bits += from_bits
        while bits >= to_bits:
            bits -= to_bits
            result.append((accumulator >> bits) & limit)
    if pad:
        if bits:
            result.append((accumulator << (to_bits - bits)) & limit)
    elif bits >= from_bits or ((accumulator << (to_bits - bits)) & limit):
        return None
    return result
