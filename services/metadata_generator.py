"""Build CHIP-0007 metadata and the license document that is hashed and uploaded.

The JSON bytes returned by :func:`metadata_document` are the bytes that must
be uploaded. The Chia ``meta_hash`` is the SHA-256 of those same bytes.
"""

from __future__ import annotations

import json
import re
import uuid
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

from utils.errors import ConfigError, ValidationError

CC0_LICENSE = """\
CC0 1.0 Universal

This work is marked with CC0 1.0.
The creator has waived all copyright and related rights to the extent allowed by law.
https://creativecommons.org/publicdomain/zero/1.0/legalcode

Minted by xd-mint-bot.
"""

_ROYALTY = re.compile(r"\d{1,3}(?:\.\d{1,2})?")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def build_chip0007(
    *,
    name: str,
    description: str,
    collection_id: str,
    collection_name: str,
    minting_tool: str,
    attributes: list[dict[str, Any]] | None = None,
    collection_attributes: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Return a CHIP-0007 document for a 1-of-1 mint."""
    collection: dict[str, Any] = {
        "id": collection_id,
        "name": collection_name,
    }
    if collection_attributes:
        collection["attributes"] = list(collection_attributes)
    return {
        "format": "CHIP-0007",
        "name": name,
        "description": description,
        "minting_tool": minting_tool,
        "sensitive_content": False,
        "series_number": 1,
        "series_total": 1,
        "attributes": list(attributes or []),
        "collection": collection,
    }


def metadata_document(metadata: dict[str, Any]) -> bytes:
    """Serialize metadata to the exact UTF-8 bytes that will be uploaded."""
    text = json.dumps(metadata, indent=2, ensure_ascii=False) + "\n"
    return text.encode("utf-8")


def license_document(license_file: str | None = None) -> bytes:
    """Return the license bytes. A custom file replaces the built-in CC0 text."""
    if license_file:
        from pathlib import Path

        path = Path(license_file)
        try:
            data = path.read_bytes()
        except OSError as exc:
            raise ConfigError(f"Could not read LICENSE_FILE: {path}") from exc
        if not data:
            raise ConfigError("LICENSE_FILE is empty.")
        return data
    return CC0_LICENSE.encode("utf-8")


def parse_collection_id(value: str) -> str:
    """Accept a UUID and return its canonical lowercase form."""
    try:
        parsed = uuid.UUID(value.strip())
    except ValueError as exc:
        raise ValidationError(
            "Collection ID must be a UUID, for example 6f1b0c3a-8e4d-4f1a-9c2b-1a2b3c4d5e6f."
        ) from exc
    return str(parsed)


def parse_royalty_percent(value: str) -> int:
    """Convert a human percent such as ``3`` or ``3.5`` into Chia hundredths."""
    cleaned = value.strip().rstrip("%").strip()
    if not _ROYALTY.fullmatch(cleaned):
        raise ValidationError(
            "Royalty must be a percent from 0 to 100 with at most two decimal places. Example: 3 or 3.5."
        )
    number = Decimal(cleaned)
    if number > 100:
        raise ValidationError("Royalty cannot be more than 100%.")
    hundredths = int((number * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    if hundredths > 10_000:
        raise ValidationError("Royalty cannot be more than 100%.")
    return hundredths


def clean_single_line(value: str, *, field: str, limit: int) -> str:
    """Strip a one-line field and reject control characters."""
    text = re.sub(r"\s+", " ", value).strip()
    if not text:
        raise ValidationError(f"{field} is required.")
    if _CONTROL.search(text):
        raise ValidationError(f"{field} contains unsupported control characters.")
    if len(text) > limit:
        raise ValidationError(f"{field} must be {limit} characters or fewer.")
    return text


def clean_description(value: str, *, limit: int = 1500, required: bool = True) -> str:
    """Normalize a description that may contain new lines."""
    text = value.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not text:
        if required:
            raise ValidationError("Description is required.")
        return ""
    if _CONTROL.search(text):
        raise ValidationError("Description contains unsupported control characters.")
    if len(text) > limit:
        raise ValidationError(f"Description must be {limit} characters or fewer.")
    return text


def parse_trait_lines(value: str) -> tuple[tuple[str, str], ...]:
    """Parse ``Trait: value`` lines into CHIP-0007 trait pairs.

    The split is the first colon or equals sign, so a value may contain more of them.
    """
    traits: list[tuple[str, str]] = []
    for raw_line in value.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = raw_line.strip()
        if not line:
            continue
        separator = ":" if ":" in line else "=" if "=" in line else ""
        if not separator:
            raise ValidationError(
                f"Attribute {line!r} needs a trait and a value, separated by a colon. Example: Background: Blue."
            )
        trait, _, raw_value = line.partition(separator)
        trait_type = clean_single_line(trait, field="Attribute name", limit=80)
        trait_value = clean_single_line(raw_value, field=f"Value for {trait_type}", limit=200)
        traits.append((trait_type, trait_value))
    if len(traits) > 25:
        raise ValidationError("A mint can have at most 25 attributes.")
    return tuple(traits)


def parse_sale_price(value: str) -> int | None:
    """Convert an XCH price to mojos. A blank value means no Dexie offer."""
    from utils.format import MOJOS_PER_XCH

    text = value.strip().lower().removesuffix("xch").strip()
    if not text:
        return None
    if not re.fullmatch(r"\d{1,7}(?:\.\d{1,12})?", text):
        raise ValidationError("Sale price must be an XCH amount such as 1.5, or left blank.")
    mojos = int((Decimal(text) * Decimal(MOJOS_PER_XCH)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    if mojos <= 0:
        raise ValidationError("Sale price must be greater than 0 XCH, or left blank to skip the offer.")
    if mojos > 1_000_000 * MOJOS_PER_XCH:
        raise ValidationError("Sale price must be 1,000,000 XCH or less.")
    return mojos


def normalize_twitter(value: str) -> str:
    """Store an X handle without a leading @ or a profile URL."""
    text = value.strip()
    if not text:
        return ""
    lowered = text.lower()
    for prefix in (
        "https://twitter.com/",
        "http://twitter.com/",
        "https://x.com/",
        "http://x.com/",
        "twitter.com/",
        "x.com/",
    ):
        if lowered.startswith(prefix):
            text = text[len(prefix) :]
            break
    text = text.split("?", 1)[0].strip().strip("/")
    if text.startswith("@"):
        text = text[1:]
    if not re.fullmatch(r"[A-Za-z0-9_]{1,15}", text):
        raise ValidationError("X handle must be 1 to 15 letters, numbers, or underscores.")
    return text


def normalize_website(value: str) -> str:
    """Require an http(s) URL for the collection website attribute."""
    from urllib.parse import urlsplit

    text = value.strip()
    if not text:
        return ""
    if not text.lower().startswith(("http://", "https://")):
        text = "https://" + text
    parts = urlsplit(text)
    if parts.scheme not in {"http", "https"} or not parts.netloc or " " in text:
        raise ValidationError("Website must be an http or https URL.")
    if len(text) > 300:
        raise ValidationError("Website URL must be 300 characters or fewer.")
    return text


def validate_mint_fields(
    *,
    name: str,
    description: str,
    royalty: str,
    attributes: str = "",
    sale_price: str = "",
    fallback_description: str,
) -> dict[str, Any]:
    """Validate modal input and return the values stored on the session.

    A blank description keeps the prefilled prompt. The collection itself comes
    from the active collection, not from this form.
    """
    cleaned_description = clean_description(description, required=False)
    if not cleaned_description:
        cleaned_description = clean_description(fallback_description)
    return {
        "name": clean_single_line(name, field="Name", limit=100),
        "description": cleaned_description,
        "royalty_hundredths": parse_royalty_percent(royalty),
        "attributes": parse_trait_lines(attributes),
        "sale_price_mojos": parse_sale_price(sale_price),
    }
